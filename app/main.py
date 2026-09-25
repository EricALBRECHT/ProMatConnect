import logging
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base, make_engine
from app.routes.api import router
from app.routes.chantiers import router as chantiers_router
from app.schema_ensure import ensure_schema
from app.services.chantiers import ChantierNotFound, ChantierService
from app.services.geocoding import FakeGeocodingService
from app.services.shopping_list import ShoppingListService
from app.services.supplier_import import SupplierImportService
from app.version import APP_VERSION
from scripts.seed import seed

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent


def _static_asset(path: str) -> str:
    """URL locale versionnée (?v=APP_VERSION) pour invalider le cache navigateur à chaque release."""
    return f"/static/{path.lstrip('/')}?v={APP_VERSION}"


def _format_money(value) -> str:
    if value is None:
        return "—"
    quantized = Decimal(str(value)).quantize(Decimal("0.01"))
    raw = f"{quantized:,.2f}"
    return raw.replace(",", "\u202f").replace(".", ",") + "\u00a0€"


def _format_qty(value) -> str:
    if value is None:
        return "—"
    quantized = Decimal(str(value)).normalize()
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _format_datetime_short(value) -> str:
    """Affichage court FR (25/09/2026 18:08) — filtre template uniquement."""
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    return dt.strftime("%d/%m/%Y %H:%M")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.settings = settings
        app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
        try:
            Base.metadata.create_all(engine)
            ensure_schema(engine)
            if settings.seed_on_start:
                with app.state.session_factory() as session:
                    seed(session)
                logger.info("ProMatConnect : catalogue simulé prêt.")
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title="ProMatConnect",
        version=APP_VERSION,
        lifespan=lifespan,
        description="Comparaison B2B de matériaux — données fictives uniquement, prix en EUR HT.",
    )
    application.include_router(router)
    application.include_router(chantiers_router)
    application.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    templates.env.globals["static_asset"] = _static_asset
    templates.env.filters["money"] = _format_money
    templates.env.filters["qty"] = _format_qty
    templates.env.filters["datetime_short"] = _format_datetime_short

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        # Ne pas journaliser la chaîne de connexion, les paramètres SQL ou les données du panier.
        logger.error("Erreur base de données : %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"detail": "Le service de données est temporairement indisponible. Réessayez."},
        )

    @application.get("/", include_in_schema=False)
    def home(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "nav_active": "compare",
                "latitude": settings.user_latitude,
                "longitude": settings.user_longitude,
                "site_address": settings.site_address,
                "company_address": settings.company_address,
                "company_latitude": settings.company_latitude,
                "company_longitude": settings.company_longitude,
                "demo_addresses": list(FakeGeocodingService.ADDRESSES),
            },
        )

    @application.get("/chantiers", include_in_schema=False)
    def chantiers_list(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="chantiers.html",
            context={"nav_active": "chantiers"},
        )

    @application.get("/chantiers/nouveau", include_in_schema=False)
    def chantiers_nouveau(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="chantier_nouveau.html",
            context={"nav_active": "chantiers"},
        )

    @application.get("/chantiers/{chantier_id}", include_in_schema=False)
    def chantiers_detail(request: Request, chantier_id: int):
        with request.app.state.session_factory() as session:
            try:
                chantier = ChantierService(session).get(chantier_id)
            except ChantierNotFound as error:
                raise HTTPException(404, str(error)) from error
        return templates.TemplateResponse(
            request=request,
            name="chantier_detail.html",
            context={"nav_active": "chantiers", "chantier": chantier},
        )

    @application.get("/chantiers/{chantier_id}/liste-achat", include_in_schema=False)
    def chantiers_liste_achat(request: Request, chantier_id: int):
        with request.app.state.session_factory() as session:
            try:
                shopping_list = ShoppingListService(session).get(chantier_id)
            except ChantierNotFound as error:
                raise HTTPException(404, str(error)) from error
        return templates.TemplateResponse(
            request=request,
            name="liste_achat.html",
            context={
                "nav_active": "chantiers",
                "liste": shopping_list,
            },
        )

    @application.get("/admin/fournisseurs", include_in_schema=False)
    def admin_fournisseurs(request: Request):
        """Page technique d'import CSV — non authentifiée (à protéger avant production)."""
        with request.app.state.session_factory() as session:
            service = SupplierImportService(session)
            sources = service.list_sources()
            catalogs = service.list_catalogs()
        return templates.TemplateResponse(
            request=request,
            name="admin_fournisseurs.html",
            context={
                "nav_active": "admin",
                "sources": sources,
                "catalogs": catalogs,
                "app_version": APP_VERSION,
                "security_note": (
                    "Page technique sans authentification — à protéger avant toute mise en production."
                ),
            },
        )

    return application


app = create_app()
