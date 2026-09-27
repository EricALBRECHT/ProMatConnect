"""Connecteur LIVE Brico Dépôt — ConnectorOffer transitoires (hors Offer DB).

Utilise SupplierLiveCacheService (magasins + offres). Soft-fail → [].
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
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
from app.connectors.bricodepot.client import (
    PRODUCT_SKU_PAGE_SIZE,
    BricoDepotClient,
    BricoDepotClientError,
    BricoProductOffer,
    BricoRetailer,
    DEFAULT_STORE_ID,
    experimental_in_store_available,
)
from app.models import Product, Supplier, SupplierProduct
from app.services.supplier_live_cache import (
    CachedLiveOffer,
    SupplierLiveCacheService,
)
from app.services.conditioning import resolve_reference_quantity
from app.services.tax import validate_vat_rate
from app.services.units import units_compatible

logger = logging.getLogger(__name__)

# Libellé live historique (ConnectorOffer.supplier / mono-fournisseur LIVE).
SUPPLIER_NAME = "BRICO DEPOT"
# Noms Supplier.name acceptés pour le mapping — imports CSV historiques utilisent
# "BRICO_DEPOT" ; le connecteur live utilisait "BRICO DEPOT". Pas de rename DB.
SUPPLIER_NAME_ALIASES: frozenset[str] = frozenset({"BRICO DEPOT", "BRICO_DEPOT"})
CONNECTOR_KEY = "api:BRICO_DEPOT"
# Namespace court (sans ':') pour agency_key, line_key et clés de cache LIVE.
AGENCY_NAMESPACE = "bricodepot"
CACHE_CONNECTOR_KEY = AGENCY_NAMESPACE


def is_brico_supplier_name(name: str | None) -> bool:
    """True si le nom catalogue correspond à Brico Dépôt (espace ou underscore)."""
    return str(name or "").strip() in SUPPLIER_NAME_ALIASES


def derive_vat_rate_from_explicit_prices(ht: Decimal, ttc: Decimal) -> Decimal | None:
    """Dérive vat_rate % depuis HT et TTC API explicites — jamais un taux inventé."""
    if ht <= 0:
        return None
    rate = ((ttc / ht) - Decimal("1")) * Decimal("100")
    rate = rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not validate_vat_rate(rate):
        return None
    return rate


def choose_source_price(
    offer: BricoProductOffer,
) -> tuple[Decimal, str, Decimal | None, str] | None:
    """Prix pièce : HT explicite requis pour une offre utilisable."""
    ht = offer.price_ht_piece.value
    ttc = offer.price_ttc_piece.value
    currency = offer.price_ht_piece.currency or offer.price_ttc_piece.currency or "EUR"
    if ht is None:
        return None
    if ttc is not None:
        return ht, "HT", derive_vat_rate_from_explicit_prices(ht, ttc), currency
    return ht, "HT", None, currency


def retailer_to_dict(retailer: BricoRetailer) -> dict[str, Any]:
    return {
        "entity_id": retailer.entity_id,
        "seller_code": retailer.seller_code,
        "name": retailer.name,
        "address": retailer.address,
        "city": retailer.city,
        "postcode": retailer.postcode,
        "latitude": retailer.latitude,
        "longitude": retailer.longitude,
        "phone": retailer.phone,
        "distance_km": retailer.distance_km,
    }


def retailer_from_dict(data: dict[str, Any]) -> BricoRetailer | None:
    try:
        entity_id = int(data["entity_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return BricoRetailer(
        entity_id=entity_id,
        seller_code=(str(data["seller_code"]).strip() if data.get("seller_code") else None) or None,
        name=(str(data["name"]).strip() if data.get("name") else None) or None,
        address=(str(data["address"]).strip() if data.get("address") else None) or None,
        city=(str(data["city"]).strip() if data.get("city") else None) or None,
        postcode=(str(data["postcode"]).strip() if data.get("postcode") else None) or None,
        latitude=float(data["latitude"]) if data.get("latitude") is not None else None,
        longitude=float(data["longitude"]) if data.get("longitude") is not None else None,
        phone=(str(data["phone"]).strip() if data.get("phone") else None) or None,
        distance_km=float(data["distance_km"]) if data.get("distance_km") is not None else None,
    )


def retailers_from_payload(payload: Any) -> list[BricoRetailer]:
    if not isinstance(payload, list):
        return []
    out: list[BricoRetailer] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        retailer = retailer_from_dict(item)
        if retailer is not None:
            out.append(retailer)
    return out


def sort_and_limit_retailers(
    retailers: list[BricoRetailer], *, max_stores: int
) -> list[BricoRetailer]:
    def sort_key(item: BricoRetailer) -> tuple[int, float]:
        if item.distance_km is None:
            return (1, 0.0)
        return (0, float(item.distance_km))

    ordered = sorted(retailers, key=sort_key)
    return ordered[: max(1, int(max_stores))]


def chunk_skus(skus: list[str], size: int = PRODUCT_SKU_PAGE_SIZE) -> list[list[str]]:
    size = max(1, min(int(size), PRODUCT_SKU_PAGE_SIZE))
    return [skus[i : i + size] for i in range(0, len(skus), size)]


def retailer_to_agency_data(retailer: BricoRetailer) -> AgencyData:
    """AgencyData à la volée — pas d'écriture Agency DB."""
    lat, lon = retailer.latitude, retailer.longitude
    if (lat is None) != (lon is None):
        lat, lon = None, None
    entity_id = int(retailer.entity_id)
    return AgencyData(
        id=entity_id,
        name=retailer.name or f"Brico Dépôt #{entity_id}",
        address=retailer.address or "",
        postal_code=retailer.postcode or "",
        city=retailer.city or "",
        latitude=lat,
        longitude=lon,
        external_id=retailer.seller_code,
        agency_key=live_agency_key(AGENCY_NAMESPACE, entity_id),
    )


class BricoDepotConnector(SupplierConnector):
    """Offres LIVE pour un code INSEE — mappings SupplierProduct + cache partagé."""

    def __init__(
        self,
        session: Session,
        *,
        insee_code: str,
        client: BricoDepotClient | None = None,
        cache: SupplierLiveCacheService | None = None,
        settings: Settings | None = None,
        supplier_name: str = SUPPLIER_NAME,
        store_id: str = DEFAULT_STORE_ID,
        preparation_minutes: int = 0,
        max_stores: int | None = None,
    ):
        self.session = session
        self.insee_code = str(insee_code).strip()
        self.settings = settings or Settings()
        self.client = client or BricoDepotClient()
        self.cache = cache or SupplierLiveCacheService(session, self.settings)
        self._supplier_name = supplier_name
        self.store_id = str(store_id)
        self.preparation_minutes = int(preparation_minutes)
        self.max_stores = int(
            max_stores if max_stores is not None else self.settings.bricodepot_max_stores
        )

    @property
    def supplier_name(self) -> str:
        return self._supplier_name

    @property
    def supplier_key(self) -> str:
        return self._supplier_name

    @property
    def connector_key(self) -> str:
        return CONNECTOR_KEY

    @property
    def display_name(self) -> str:
        return self._supplier_name

    @property
    def source_type(self) -> str:
        return "api"

    def health(self) -> ConnectorHealth:
        try:
            _ = self.client.product_query
            _ = self.client.stores_query
            if not self.insee_code:
                return ConnectorHealth(
                    ok=False,
                    connector_key=self.connector_key,
                    supplier_key=self.supplier_key,
                    source_type=self.source_type,
                    detail="code INSEE manquant",
                )
            return ConnectorHealth(
                ok=True,
                connector_key=self.connector_key,
                supplier_key=self.supplier_key,
                source_type=self.source_type,
                detail=f"insee={self.insee_code}",
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorHealth(
                ok=False,
                connector_key=self.connector_key,
                supplier_key=self.supplier_key,
                source_type=self.source_type,
                detail=str(exc),
            )

    def _load_mapped_products(
        self, product_ids: list[int]
    ) -> list[tuple[SupplierProduct, Product]]:
        if not product_ids:
            return []
        statement = (
            select(SupplierProduct, Product)
            .join(Supplier, SupplierProduct.supplier_id == Supplier.id)
            .join(Product, SupplierProduct.product_id == Product.id)
            .where(
                Supplier.name.in_(tuple(SUPPLIER_NAME_ALIASES)),
                SupplierProduct.active.is_(True),
                SupplierProduct.product_id.is_not(None),
                SupplierProduct.product_id.in_(product_ids),
            )
            .order_by(SupplierProduct.id)
        )
        rows: list[tuple[SupplierProduct, Product]] = []
        for sp, product in self.session.execute(statement):
            if not units_compatible(product.reference_unit, sp.reference_unit):
                continue
            rows.append((sp, product))
        return rows

    def get_offers(self, product_ids: list[int]) -> list[ConnectorOffer]:
        try:
            return self._get_offers_unsafe(product_ids)
        except BricoDepotClientError as exc:
            logger.warning("Brico Dépôt LIVE indisponible (%s): %s", self.insee_code, exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Brico Dépôt LIVE erreur inattendue (insee=%s): %s",
                self.insee_code,
                exc,
            )
            return []

    def _get_offers_unsafe(self, product_ids: list[int]) -> list[ConnectorOffer]:
        requested = sorted({int(pid) for pid in product_ids})
        logger.info(
            "Brico live: citycode=%s requested_products=%s",
            self.insee_code,
            len(requested),
        )
        mapped = self._load_mapped_products(product_ids)
        logger.info("Brico live: mapped_products=%s", len(mapped))
        if not mapped:
            return []
        sku_to_sp: dict[str, tuple[SupplierProduct, Product]] = {}
        for sp, product in mapped:
            sku = str(sp.supplier_reference).strip()
            if sku and sku not in sku_to_sp:
                sku_to_sp[sku] = (sp, product)
        skus = list(sku_to_sp.keys())
        if not skus:
            return []

        retailers = self._resolve_retailers()
        logger.info("Brico live: stores=%s", len(retailers))
        if not retailers:
            return []

        now = datetime.now(timezone.utc)
        offers: list[ConnectorOffer] = []
        for retailer in retailers:
            agency = retailer_to_agency_data(retailer)
            store_id = str(retailer.entity_id)
            self._refresh_store_skus(retailer, skus)
            for sku, sp_pair in sku_to_sp.items():
                cached = self._load_cached_offer(store_id, sku)
                if cached is None:
                    continue
                built = self._build_from_cache(
                    cached=cached,
                    sp=sp_pair[0],
                    product=sp_pair[1],
                    agency=agency,
                    updated_at=now,
                )
                if built is not None:
                    offers.append(built)
        logger.info("Brico live: offers=%s", len(offers))
        return offers

    def _resolve_retailers(self) -> list[BricoRetailer]:
        hit = self.cache.get_stores(CACHE_CONNECTOR_KEY, self.insee_code)
        if hit is not None:
            return sort_and_limit_retailers(
                retailers_from_payload(hit.payload), max_stores=self.max_stores
            )

        if self.cache.try_acquire_store_refresh_lease(CACHE_CONNECTOR_KEY, self.insee_code):
            try:
                retailers = self.client.fetch_retailers(self.insee_code)
                payload = [retailer_to_dict(r) for r in retailers]
                self.cache.put_stores(CACHE_CONNECTOR_KEY, self.insee_code, payload)
                return sort_and_limit_retailers(retailers, max_stores=self.max_stores)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Brico store locator échec (insee=%s): %s", self.insee_code, exc
                )
                self.cache.release_store_refresh_lease(CACHE_CONNECTOR_KEY, self.insee_code)
                stale = self.cache.get_stores(
                    CACHE_CONNECTOR_KEY, self.insee_code, allow_stale=True
                )
                if stale is None:
                    return []
                return sort_and_limit_retailers(
                    retailers_from_payload(stale.payload), max_stores=self.max_stores
                )

        stale = self.cache.get_stores(CACHE_CONNECTOR_KEY, self.insee_code, allow_stale=True)
        if stale is None:
            return []
        return sort_and_limit_retailers(
            retailers_from_payload(stale.payload), max_stores=self.max_stores
        )

    def _refresh_store_skus(self, retailer: BricoRetailer, skus: list[str]) -> None:
        store_id = str(retailer.entity_id)
        plan = self.cache.plan_offer_refresh(CACHE_CONNECTOR_KEY, store_id, skus)
        to_refresh = list(plan.skus_to_refresh)
        if not to_refresh:
            return
        won = list(
            self.cache.try_acquire_offer_refresh_leases(
                CACHE_CONNECTOR_KEY, store_id, to_refresh
            )
        )
        if not won:
            return
        for batch in chunk_skus(won, PRODUCT_SKU_PAGE_SIZE):
            try:
                by_sku = self.client.fetch_products_by_sku(
                    seller_id=retailer.entity_id,
                    skus=batch,
                    store_id=self.store_id,
                )
            except BricoDepotClientError as exc:
                logger.warning(
                    "Brico produits échec (seller=%s): %s", retailer.entity_id, exc
                )
                for sku in batch:
                    self.cache.release_offer_refresh_lease(
                        CACHE_CONNECTOR_KEY, store_id, sku
                    )
                continue
            for sku in batch:
                product_offer = by_sku.get(sku)
                if product_offer is None or product_offer.price_ht_piece.value is None:
                    # Ne pas écraser une bonne entrée avec une réponse invalide.
                    self.cache.release_offer_refresh_lease(
                        CACHE_CONNECTOR_KEY, store_id, sku
                    )
                    continue
                try:
                    self.cache.put_offer(
                        CACHE_CONNECTOR_KEY,
                        store_id,
                        sku,
                        seller_code=retailer.seller_code,
                        price_ht=product_offer.price_ht_piece.value,
                        price_ttc=product_offer.price_ttc_piece.value,
                        currency=(
                            product_offer.price_ht_piece.currency
                            or product_offer.price_ttc_piece.currency
                            or "EUR"
                        ),
                        stock_quantity=product_offer.stock_quantity,
                        stock_status=product_offer.stock_status,
                        is_salable=product_offer.is_salable,
                        is_offer_available=product_offer.is_offer_available,
                        update_price=True,
                        update_stock=True,
                    )
                except ValueError:
                    self.cache.release_offer_refresh_lease(
                        CACHE_CONNECTOR_KEY, store_id, sku
                    )

    def _load_cached_offer(self, store_id: str, sku: str) -> CachedLiveOffer | None:
        entry = self.cache.get_offer(CACHE_CONNECTOR_KEY, store_id, sku)
        if entry is not None and entry.price_ht is not None:
            return entry
        stale = self.cache.get_offer(
            CACHE_CONNECTOR_KEY, store_id, sku, allow_stale=True
        )
        if stale is not None and stale.price_ht is not None:
            return stale
        return None

    def _build_from_cache(
        self,
        *,
        cached: CachedLiveOffer,
        sp: SupplierProduct,
        product: Product,
        agency: AgencyData,
        updated_at: datetime,
    ) -> ConnectorOffer | None:
        ht = cached.price_ht
        if ht is None:
            return None
        ttc = cached.price_ttc
        vat_rate = None
        if ttc is not None:
            vat_rate = derive_vat_rate_from_explicit_prices(ht, ttc)
        usable = experimental_in_store_available(
            stock_quantity=cached.stock_quantity,
            is_salable=cached.is_salable,
            is_offer_available=cached.is_offer_available,
        )
        qty = cached.stock_quantity
        stock = int(qty) if usable and qty is not None and qty > 0 else 0
        ref_qty = resolve_reference_quantity(sp)
        pack_qty = sp.packaging_quantity if sp.packaging_quantity is not None else Decimal("1")
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
            stock=stock,
            preparation_minutes=self.preparation_minutes,
            updated_at=updated_at,
            image_url=sp.image_url,
        )

    def _build_connector_offer(
        self,
        *,
        product_offer: BricoProductOffer,
        sp: SupplierProduct,
        product: Product,
        agency: AgencyData,
        updated_at: datetime,
    ) -> ConnectorOffer | None:
        """Conservé pour tests unitaires / chemins directs hors cache."""
        priced = choose_source_price(product_offer)
        if priced is None:
            return None
        price, tax_basis, vat_rate, currency = priced
        usable = experimental_in_store_available(
            stock_quantity=product_offer.stock_quantity,
            is_salable=product_offer.is_salable,
            is_offer_available=product_offer.is_offer_available,
        )
        qty = product_offer.stock_quantity
        stock = int(qty) if usable and qty is not None and qty > 0 else 0
        ref_qty = resolve_reference_quantity(sp)
        pack_qty = sp.packaging_quantity if sp.packaging_quantity is not None else Decimal("1")
        return ConnectorOffer(
            supplier=self._supplier_name,
            agency=agency,
            product_id=int(sp.product_id),
            supplier_reference=sp.supplier_reference,
            supplier_unit=sp.supplier_unit,
            reference_quantity=ref_qty,
            reference_unit=sp.reference_unit or product.reference_unit,
            packaging_quantity=pack_qty,
            price=price,
            tax_basis=tax_basis,
            vat_rate=vat_rate,
            currency=currency,
            stock=stock,
            preparation_minutes=self.preparation_minutes,
            updated_at=updated_at,
            image_url=sp.image_url,
        )
