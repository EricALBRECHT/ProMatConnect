"""Connecteur LIVE Gedimat — offres d'un magasin, hors table Offer.

Le SupplierProduct est la référence catalogue (tellus_variant).
Le prix et le stock dépendent du store_id et vont dans le cache live.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import (
    AgencyData,
    ConnectorHealth,
    ConnectorOffer,
    SupplierConnector,
    live_agency_key,
)
from app.connectors.gedimat.availability import (
    AVAILABLE,
    DELAYED,
    ORDER_ONLY,
    UNAVAILABLE,
    normalize_availability,
    purchasable_quantity,
)
from app.connectors.gedimat.public_client import GedimatPublicClient
from app.connectors.bricodepot.connector import derive_vat_rate_from_explicit_prices
from app.models import Product, Supplier, SupplierProduct
from app.services.conditioning import resolve_reference_quantity
from app.services.supplier_live_cache import CachedLiveOffer, SupplierLiveCacheService
from app.services.tax import validate_vat_rate
from app.services.units import units_compatible

logger = logging.getLogger(__name__)

SUPPLIER_NAME = "GEDIMAT"
CONNECTOR_KEY = "api:GEDIMAT"
CACHE_CONNECTOR_KEY = "GEDIMAT"
AGENCY_NAMESPACE = "gedimat"


@dataclass(frozen=True)
class GedimatLiveStore:
    """Magasin interrogé en live. Le cache est indexé par algolia_store_id."""

    gedimat_id: int
    algolia_store_id: int
    name: str
    address: str = ""
    postal_code: str = ""
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None


class GedimatConnector(SupplierConnector):
    def __init__(
        self,
        session: Session,
        *,
        store_id: int | None = None,
        stores: list[GedimatLiveStore] | None = None,
        client: GedimatPublicClient | None = None,
        cache: SupplierLiveCacheService | None = None,
        settings: Settings | None = None,
        preparation_minutes: int = 0,
    ):
        if stores:
            self.stores = list(stores)
        elif store_id is not None and int(store_id) > 0:
            self.stores = [
                GedimatLiveStore(
                    gedimat_id=int(store_id),
                    algolia_store_id=int(store_id),
                    name=f"Gedimat {int(store_id)}",
                )
            ]
        else:
            raise ValueError("store_id Gedimat requis.")
        self.store_id = int(self.stores[0].algolia_store_id)
        self.session = session
        self.settings = settings or Settings()
        self.cache = cache or SupplierLiveCacheService(session, self.settings)
        self.client = client or GedimatPublicClient.from_public_page()
        self.preparation_minutes = preparation_minutes
        self._supplier_name = SUPPLIER_NAME
        self._cache_written = False
        self._offer_origin: dict[tuple[str, str], str] = {}

    @property
    def supplier_name(self) -> str:
        return self._supplier_name

    @property
    def connector_key(self) -> str:
        return CONNECTOR_KEY

    @property
    def source_type(self) -> str:
        return "api"

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            ok=True,
            connector_key=self.connector_key,
            supplier_key=self.supplier_key,
            source_type=self.source_type,
            detail=f"stores={len(self.stores)}",
        )

    def get_offers(
        self, product_ids: list[int], *, force_refresh: bool = False
    ) -> list[ConnectorOffer]:
        self._cache_written = False
        self._offer_origin = {}
        try:
            offers = self._get_offers(product_ids, force_refresh=force_refresh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gedimat LIVE erreur (store=%s): %s", self.store_id, exc)
            return []
        if self._cache_written:
            self.session.commit()
        return offers

    def _live_ttl_s(self) -> int:
        return int(self.settings.gedimat_live_cache_ttl_seconds)

    def _get_offers(
        self, product_ids: list[int], *, force_refresh: bool
    ) -> list[ConnectorOffer]:
        rows = self._mapped_rows(product_ids)
        if not rows:
            return []
        skus = [sp.supplier_reference for sp, _product in rows]
        now = datetime.now(timezone.utc)
        offers: list[ConnectorOffer] = []
        for store in self.stores:
            store_key = str(store.algolia_store_id)
            plan = self.cache.plan_offer_refresh(CACHE_CONNECTOR_KEY, store_key, skus)
            to_refresh = list(skus) if force_refresh else list(plan.skus_to_refresh)
            if to_refresh:
                self._refresh(store_key, to_refresh)
            agency = self._agency(store)
            refreshing = set(to_refresh)
            for sp, product in rows:
                sku = sp.supplier_reference
                cached = self._load_cached(store_key, sku)
                if cached is None:
                    continue
                origin = self._offer_origin.get((store_key, sku))
                if origin is None:
                    origin = "cache" if sku not in refreshing else "stale"
                built = self._build(cached, sp, product, agency, now, origin)
                if built is not None:
                    offers.append(built)
        return offers

    def _refresh(self, store_key: str, skus: list[str]) -> None:
        won = list(
            self.cache.try_acquire_offer_refresh_leases(
                CACHE_CONNECTOR_KEY, store_key, skus
            )
        )
        if not won:
            return
        try:
            hits = self.client.lookup_skus(int(store_key), won)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gedimat lookup échec (store=%s): %s", store_key, exc)
            for sku in won:
                self.cache.release_offer_refresh_lease(CACHE_CONNECTOR_KEY, store_key, sku)
                self._offer_origin[(store_key, sku)] = "stale"
            return
        ttl = self._live_ttl_s()
        for sku in won:
            hit = hits.get(sku)
            if hit is None or not self._store_hit(store_key, sku, hit, ttl):
                self.cache.release_offer_refresh_lease(CACHE_CONNECTOR_KEY, store_key, sku)
                if hit is None:
                    self._offer_origin.setdefault((store_key, sku), "stale")

    def _store_hit(self, store_key: str, sku: str, hit: dict[str, Any], ttl: int) -> bool:
        kind = normalize_availability(hit)
        prix = hit.get("prix") if isinstance(hit.get("prix"), dict) else {}
        ht = _money(prix.get("ht"))
        ttc = _money(prix.get("ttc"))
        negative = kind == UNAVAILABLE
        if ht is None and not negative:
            return False
        qty = purchasable_quantity(hit)
        extras = {
            "stock": hit.get("stock"),
            "dispo": hit.get("dispo"),
            "quantite_stock": hit.get("quantite_stock"),
            "is_available": hit.get("is_available"),
            "availability": hit.get("availability"),
            "vendable": hit.get("vendable"),
            "fulfillment": kind,
            "ht_pro": prix.get("ht_pro"),
            "tva": hit.get("tva"),
        }
        try:
            self.cache.put_offer(
                CACHE_CONNECTOR_KEY,
                store_key,
                sku,
                price_ht=ht,
                price_ttc=ttc,
                currency="EUR",
                stock_quantity=qty if ht is not None else 0,
                stock_status=kind,
                is_salable=bool(hit.get("vendable")) if hit.get("vendable") is not None else None,
                is_offer_available=(
                    bool(hit.get("is_available")) if hit.get("is_available") is not None else None
                ),
                extras=extras,
                update_price=ht is not None,
                update_stock=True,
                price_ttl_s=ttl,
                stock_ttl_s=ttl,
            )
        except ValueError:
            return False
        self._cache_written = True
        self._offer_origin[(store_key, sku)] = "live"
        return True

    def _load_cached(self, store_key: str, sku: str) -> CachedLiveOffer | None:
        entry = self.cache.get_offer(CACHE_CONNECTOR_KEY, store_key, sku)
        if entry is not None and (entry.price_ht is not None or entry.stock_status):
            return entry
        stale = self.cache.get_offer(
            CACHE_CONNECTOR_KEY, store_key, sku, allow_stale=True
        )
        if stale is not None and (stale.price_ht is not None or stale.stock_status):
            return stale
        return None

    def _build(
        self,
        cached: CachedLiveOffer,
        sp: SupplierProduct,
        product: Product,
        agency: AgencyData,
        updated_at: datetime,
        live_status: str,
    ) -> ConnectorOffer | None:
        kind = (cached.stock_status or "").strip().lower()
        if kind not in {AVAILABLE, DELAYED, ORDER_ONLY, UNAVAILABLE}:
            extras = cached.extras or {}
            kind = str(extras.get("fulfillment") or UNAVAILABLE)
        ht = cached.price_ht
        if ht is None and kind != UNAVAILABLE:
            return None
        if ht is None:
            ht = Decimal("0")
        ttc = cached.price_ttc
        vat_rate = _vat(cached.extras, ht, ttc)
        if not units_compatible(product.reference_unit, sp.reference_unit or product.reference_unit):
            return None
        ref_qty = resolve_reference_quantity(sp)
        pack_qty = sp.packaging_quantity if sp.packaging_quantity is not None else Decimal("1")
        stock = int(cached.stock_quantity or 0)
        if kind == AVAILABLE and stock <= 0:
            stock = 0
        fetched = cached.price_fetched_at or cached.stock_fetched_at
        return ConnectorOffer(
            supplier=self._supplier_name,
            agency=agency,
            product_id=int(sp.product_id),
            supplier_reference=sp.supplier_reference,
            supplier_unit=sp.supplier_unit,
            reference_quantity=ref_qty,
            reference_unit=sp.reference_unit or product.reference_unit,
            packaging_quantity=pack_qty,
            price=ht,
            tax_basis="HT",
            vat_rate=vat_rate,
            currency=cached.currency or "EUR",
            stock=max(stock, 0),
            preparation_minutes=self.preparation_minutes,
            updated_at=updated_at,
            image_url=sp.image_url,
            live_status=live_status,
            fetched_at=fetched,
            fulfillment=kind,
        )

    def _mapped_rows(self, product_ids: list[int]) -> list[tuple[SupplierProduct, Product]]:
        ids = [int(pid) for pid in product_ids if pid is not None]
        if not ids:
            return []
        supplier_id = self.session.scalar(
            select(Supplier.id).where(Supplier.name == SUPPLIER_NAME)
        )
        if supplier_id is None:
            return []
        pairs = self.session.execute(
            select(SupplierProduct, Product)
            .join(Product, Product.id == SupplierProduct.product_id)
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.product_id.in_(ids),
                SupplierProduct.active.is_(True),
            )
        ).all()
        return [(sp, product) for sp, product in pairs if sp.product_id is not None]

    def _agency(self, store: GedimatLiveStore) -> AgencyData:
        label = store.name.strip() or f"Gedimat {store.gedimat_id}"
        if store.city and store.city.casefold() not in label.casefold():
            label = f"{label} {store.city}".strip()
        return AgencyData(
            id=int(store.gedimat_id),
            name=label,
            address=store.address or "",
            postal_code=store.postal_code or "",
            city=store.city or "",
            latitude=store.latitude,
            longitude=store.longitude,
            external_id=str(store.algolia_store_id),
            agency_key=live_agency_key(AGENCY_NAMESPACE, store.gedimat_id),
        )


def _money(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except Exception:
        return None
    if amount < 0:
        return None
    return amount.quantize(Decimal("0.01"))


def _vat(extras: dict | None, ht: Decimal, ttc: Decimal | None) -> Decimal | None:
    raw = (extras or {}).get("tva")
    if raw is not None:
        try:
            rate = Decimal(str(raw)).quantize(Decimal("0.01"))
        except Exception:
            rate = None
        if rate is not None and validate_vat_rate(rate):
            return rate
    if ttc is None or ht <= 0:
        return None
    return derive_vat_rate_from_explicit_prices(ht, ttc)
