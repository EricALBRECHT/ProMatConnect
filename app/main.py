import logging
from contextlib import asynccontextmanager
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
from app.services.chantiers import ChantierNotFound, ChantierService
from app.services.geocoding import FakeGeocodingService
from scripts.seed import seed

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.settings = settings
        app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
        try:
            Base.metadata.create_all(engine)
            if settings.seed_on_start:
                with app.state.session_factory() as session:
                    seed(session)
                logger.info("ProMatConnect : catalogue simulé prêt.")
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title="ProMatConnect",
        version="0.2.0",
        lifespan=lifespan,
        description="Comparaison B2B de matériaux — données fictives uniquement, prix en EUR HT.",
    )
    application.include_router(router)
    application.include_router(chantiers_router)
    application.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

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

    return application


app = create_app()
