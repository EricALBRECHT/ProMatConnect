from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import SupplierConnector
from app.connectors.bricodepot.connector import (
    SUPPLIER_NAME_ALIASES,
    BricoDepotConnector,
    is_brico_supplier_name,
)
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


def _supplier_participates(session: Session, *names: str) -> bool:
    """Un connecteur nommé est construit si aucune fiche n'existe, ou si l'une est active.

    L'absence de ligne ne bloque pas (live avant import). Une fiche inactive bloque
    ce fournisseur, quel que soit son nom.
    """
    rows = list(session.scalars(select(Supplier).where(Supplier.name.in_(tuple(names)))))
    if not rows:
        return True
    return any(row.active for row in rows)


def build_connectors(session: Session) -> list[SupplierConnector]:
    """Un connecteur catalogue interne par fournisseur présent en base."""
    repository = OfferRepository(session)
    suppliers = list(session.scalars(select(Supplier)))
    suppliers.sort(key=lambda s: (_PREFERRED_ORDER.get(s.name, 100), s.name))
    connectors: list[SupplierConnector] = []
    for supplier in suppliers:
        if not supplier.active:
            continue
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
    gedimat_client=None,
) -> list[SupplierConnector]:
    """Connecteurs LIVE (Brico Dépôt, …) — désactivés par défaut.

    Retourne [] si flag off, citycode absent, ou config insuffisante.
    `brico_client` injectable pour tests offline.
    """
    from app.connectors.gedimat.connector import SUPPLIER_NAME as GEDIMAT_SUPPLIER_NAME

    connectors: list[SupplierConnector] = []
    if settings.gedimat_live_enabled and _supplier_participates(session, GEDIMAT_SUPPLIER_NAME):
        gedimat = _gedimat_live_connector(
            session,
            resolved_origin=resolved_origin,
            settings=settings,
            client=gedimat_client,
        )
        if gedimat is not None:
            connectors.append(gedimat)
    if not settings.bricodepot_live_enabled:
        return connectors
    if not _supplier_participates(session, *sorted(SUPPLIER_NAME_ALIASES)):
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


def _gedimat_live_connector(
    session: Session,
    *,
    resolved_origin: ResolvedOrigin | None,
    settings: Settings,
    client,
):
    """Un connecteur, plusieurs magasins e-commerce proches. Sans coordonnées : repli store_id."""
    from app.connectors.gedimat.connector import GedimatConnector, GedimatLiveStore
    from app.services.gedimat_stores import nearest_ecommerce_gedimat_stores, resolve_connector_store_id

    cache = SupplierLiveCacheService(session, settings)
    if resolved_origin is not None:
        nearest = nearest_ecommerce_gedimat_stores(
            session,
            resolved_origin.latitude,
            resolved_origin.longitude,
            limit=settings.gedimat_nearest_store_limit,
        )
        if nearest:
            return GedimatConnector(
                session,
                stores=[
                    GedimatLiveStore(
                        gedimat_id=store.gedimat_id,
                        algolia_store_id=int(store.algolia_store_id),
                        name=store.name,
                        address=store.address,
                        postal_code=store.postal_code,
                        city=store.city,
                        latitude=float(store.latitude),
                        longitude=float(store.longitude),
                    )
                    for store in nearest
                ],
                client=client,
                cache=cache,
                settings=settings,
            )
    store_id = settings.gedimat_store_id
    if not store_id:
        return None
    algolia_store_id = resolve_connector_store_id(session, int(store_id))
    if not algolia_store_id:
        return None
    return GedimatConnector(
        session,
        store_id=int(algolia_store_id),
        client=client,
        cache=cache,
        settings=settings,
    )


def build_comparison_connectors(
    session: Session,
    *,
    resolved_origin: ResolvedOrigin | None,
    settings: Settings,
    brico_client=None,
    gedimat_client=None,
) -> list[SupplierConnector]:
    """Connecteurs pour /compare : catalogue + live, sans doublon d'enseigne.

    - Si Brico LIVE est actif : on n'expose pas le connecteur file ``BRICO_DEPOT``
      (même enseigne, autre identité technique) — un seul « Tout chez Brico Dépôt ».
    - Un fournisseur ``active=false`` n'a aucun connecteur, fichier ou live.
    """
    live = build_live_connectors(
        session,
        resolved_origin=resolved_origin,
        settings=settings,
        brico_client=brico_client,
        gedimat_client=gedimat_client,
    )
    catalog = build_connectors(session)
    brico_live = any(connector.connector_key == "api:BRICO_DEPOT" for connector in live)
    gedimat_live = any(connector.connector_key == "api:GEDIMAT" for connector in live)
    if brico_live:
        catalog = [c for c in catalog if not is_brico_supplier_name(c.supplier_name)]
    if gedimat_live:
        catalog = [c for c in catalog if c.supplier_name != "GEDIMAT"]
    return [*catalog, *live]
