"""Synchronisation du référentiel magasins Gedimat et recherche par distance.

N'écrit ni Product, ni SupplierProduct, ni offre. Un magasin absent d'un
passage suivant n'est pas supprimé.
"""

from __future__ import annotations

import logging
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.gedimat.stores import (
    GedimatStoreDirectory,
    PublicGedimatStore,
    own_catalog_id,
)
from app.models.gedimat_store import GedimatStore
from app.services.distance import haversine_km

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GedimatStoreSyncResult:
    created: int
    updated: int
    unchanged: int
    algolia_failed: int


@dataclass(frozen=True)
class NearestGedimatStore:
    gedimat_id: int
    algolia_store_id: int | None
    name: str
    address: str
    postal_code: str
    city: str
    distance_km: float
    ecommerce: bool
    latitude: Decimal
    longitude: Decimal
    store_type: str | None


def sync_gedimat_stores(
    session: Session,
    *,
    directory: GedimatStoreDirectory | None = None,
    workers: int = 2,
    listing: list[PublicGedimatStore] | None = None,
    assignments: dict[int, tuple[int | None, bool | None]] | None = None,
) -> GedimatStoreSyncResult:
    """Insert ou met à jour. `listing` et `assignments` évitent le réseau dans les tests."""
    client = directory or GedimatStoreDirectory()
    stores = listing if listing is not None else client.fetch_listing()
    if assignments is None:
        resolved, failed = _resolve_assignments(client, stores, workers)
    else:
        resolved, failed = assignments, 0
    created = updated = unchanged = 0
    for store in stores:
        assignment = resolved.get(store.gedimat_id)
        if assignment is None:
            catalog_id = _UNRESOLVED
        else:
            algolia_id, page_ecommerce = assignment
            catalog_id = own_catalog_id(
                ecommerce=store.ecommerce,
                algolia_id=algolia_id,
                page_ecommerce=page_ecommerce,
            )
        action = _upsert(session, store, catalog_id)
        if action == "created":
            created += 1
        elif action == "updated":
            updated += 1
        else:
            unchanged += 1
    session.commit()
    return GedimatStoreSyncResult(created, updated, unchanged, failed)


def nearest_ecommerce_gedimat_stores(
    session: Session,
    latitude: float,
    longitude: float,
    limit: int = 5,
) -> list[NearestGedimatStore]:
    """Les N magasins e-commerce les plus proches qui ont un store_id Algolia."""
    if limit <= 0:
        return []
    rows = session.scalars(
        select(GedimatStore).where(
            GedimatStore.active.is_(True),
            GedimatStore.ecommerce.is_(True),
            GedimatStore.algolia_store_id.is_not(None),
            GedimatStore.latitude.is_not(None),
            GedimatStore.longitude.is_not(None),
        )
    ).all()
    ranked: list[NearestGedimatStore] = []
    for row in rows:
        distance = haversine_km(latitude, longitude, float(row.latitude), float(row.longitude))
        ranked.append(
            NearestGedimatStore(
                gedimat_id=row.gedimat_id,
                algolia_store_id=row.algolia_store_id,
                name=row.name,
                address=row.address,
                postal_code=row.postal_code,
                city=row.city,
                distance_km=distance,
                ecommerce=True,
                latitude=row.latitude,
                longitude=row.longitude,
                store_type=row.store_type,
            )
        )
    ranked.sort(key=lambda item: (item.distance_km, item.gedimat_id))
    return ranked[:limit]


def get_nearest_gedimat_stores(
    session: Session,
    latitude: float,
    longitude: float,
    limit: int = 5,
) -> list[NearestGedimatStore]:
    """Magasins actifs géolocalisés, du plus proche au plus loin. Pas branché au comparateur."""
    if limit <= 0:
        return []
    rows = session.scalars(
        select(GedimatStore).where(
            GedimatStore.active.is_(True),
            GedimatStore.latitude.is_not(None),
            GedimatStore.longitude.is_not(None),
        )
    ).all()
    ranked: list[NearestGedimatStore] = []
    for row in rows:
        distance = haversine_km(latitude, longitude, float(row.latitude), float(row.longitude))
        ranked.append(
            NearestGedimatStore(
                gedimat_id=row.gedimat_id,
                algolia_store_id=row.algolia_store_id,
                name=row.name,
                address=row.address,
                postal_code=row.postal_code,
                city=row.city,
                distance_km=distance,
                ecommerce=row.ecommerce,
                latitude=row.latitude,
                longitude=row.longitude,
                store_type=row.store_type,
            )
        )
    ranked.sort(key=lambda item: (item.distance_km, item.gedimat_id))
    return ranked[:limit]


def resolve_connector_store_id(session: Session, configured_id: int) -> int | None:
    """Identifiant passé au connecteur live : le store_id Algolia, pas gedimat_id.

    Sans ligne de référentiel, la valeur configurée est conservée (comportement
    historique, déjà un store_id Algolia pour le magasin de démonstration).
    """
    row = session.scalar(select(GedimatStore).where(GedimatStore.gedimat_id == int(configured_id)))
    if row is None:
        return int(configured_id)
    return row.algolia_store_id


def _resolve_assignments(
    client: GedimatStoreDirectory,
    listing: list[PublicGedimatStore],
    workers: int,
) -> tuple[dict[int, tuple[int | None, bool | None]], int]:
    resolved: dict[int, tuple[int | None, bool | None] | None] = {}
    failed = 0

    def one(gedimat_id: int) -> tuple[int, tuple[int | None, bool | None] | None]:
        try:
            assignment = client.fetch_assignment(gedimat_id)
            return gedimat_id, (assignment.algolia_id, assignment.page_ecommerce)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Gedimat magasin %s : algoliaIdM illisible (%s)", gedimat_id, exc)
            return gedimat_id, None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, store.gedimat_id) for store in listing]
        done = 0
        for future in as_completed(futures):
            gedimat_id, assignment = future.result()
            done += 1
            if assignment is None:
                failed += 1
                resolved[gedimat_id] = None
            else:
                resolved[gedimat_id] = assignment
            if done % 50 == 0:
                logger.info("Gedimat magasins : %s/%s algoliaIdM", done, len(listing))
    return resolved, failed


class _Unresolved:
    """Lecture algoliaIdM échouée : ne pas écraser un store_id déjà connu."""


_UNRESOLVED = _Unresolved()


def _upsert(session: Session, store: PublicGedimatStore, catalog_id: int | None | _Unresolved) -> str:
    row = session.scalar(select(GedimatStore).where(GedimatStore.gedimat_id == store.gedimat_id))
    if row is None:
        session.add(
            GedimatStore(
                gedimat_id=store.gedimat_id,
                algolia_store_id=None if catalog_id is _UNRESOLVED else catalog_id,
                name=store.name,
                address=store.address,
                postal_code=store.postal_code,
                city=store.city,
                latitude=store.latitude,
                longitude=store.longitude,
                ecommerce=store.ecommerce,
                store_type=store.store_type,
                active=True,
            )
        )
        return "created"
    changed = False
    fields: list[tuple[str, object]] = [
        ("name", store.name),
    ]
    if catalog_id is not _UNRESOLVED:
        fields.append(("algolia_store_id", catalog_id))
    fields.extend(
        (
            ("address", store.address),
            ("postal_code", store.postal_code),
            ("city", store.city),
            ("latitude", store.latitude),
            ("longitude", store.longitude),
            ("ecommerce", store.ecommerce),
            ("store_type", store.store_type),
            ("active", True),
        )
    )
    for field, value in fields:
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    return "updated" if changed else "unchanged"
