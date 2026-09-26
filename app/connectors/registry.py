from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import SupplierConnector
from app.connectors.bricodepot.connector import BricoDepotConnector
from app.connectors.demo import DemoSupplierConnector
from app.models import Offer, Supplier
from app.repositories.offers import OfferRepository
from app.schemas.location import ResolvedOrigin
from app.services.supplier_live_cache import SupplierLiveCacheService

# Ordre historique du comparateur (options mono-fournisseur).
_PREFERRED_ORDER = {"POINT.P TEST": 0, "GEDIMAT TEST": 1}


def _source_type_for(session: Session, supplier_id: int, fallback: str) -> str:
    """Déduit la provenance dominante des offres (file > api > demo)."""
    types = set(
        session.scalars(select(Offer.source_type).where(Offer.supplier_id == supplier_id).distinct())
    )
    types.discard(None)
    if "file" in types:
        return "file"
    if "api" in types:
        return "api"
    if "demo" in types:
        return "demo"
    return fallback


def build_connectors(session: Session) -> list[SupplierConnector]:
    """Un connecteur catalogue interne par fournisseur présent en base."""
    repository = OfferRepository(session)
    suppliers = list(session.scalars(select(Supplier)))
    suppliers.sort(key=lambda s: (_PREFERRED_ORDER.get(s.name, 100), s.name))
    connectors: list[SupplierConnector] = []
    for supplier in suppliers:
        source = supplier.source_type or _source_type_for(session, supplier.id, "demo")
        prefix = "demo" if source == "demo" else source
        connectors.append(
            DemoSupplierConnector(
                repository,
                supplier.name,
                source_type=source,
                connector_prefix=prefix,
            )
        )
    return connectors


def build_live_connectors(
    session: Session,
    *,
    resolved_origin: ResolvedOrigin | None,
    settings: Settings,
    brico_client=None,
) -> list[SupplierConnector]:
    """Connecteurs LIVE (Brico Dépôt, …) — désactivés par défaut.

    Retourne [] si flag off, citycode absent, ou config insuffisante.
    `brico_client` injectable pour tests offline.
    """
    connectors: list[SupplierConnector] = []
    if not settings.bricodepot_live_enabled:
        return connectors
    citycode = (resolved_origin.citycode if resolved_origin else None) or ""
    citycode = str(citycode).strip()
    if not citycode:
        return connectors
    cache = SupplierLiveCacheService(session, settings)
    connectors.append(
        BricoDepotConnector(
            session,
            insee_code=citycode,
            client=brico_client,
            cache=cache,
            settings=settings,
            max_stores=settings.bricodepot_max_stores,
        )
    )
    return connectors
