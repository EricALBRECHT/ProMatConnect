"""Cache serveur partagé — données fournisseurs LIVE (magasins + offres).

Usage prévu par les connecteurs live (Brico Dépôt, Point.P, …) :
- pas de cache par artisan ;
- distinct des snapshots ApprovisionnementRetenu ;
- distinct du géocodage.

Fraîcheur prix / stock indépendante sur chaque ligne offre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.supplier_live_cache import SupplierOfferCache, SupplierStoreCache

# Clés interdites dans les payloads JSON (défense en profondeur).
_SENSITIVE_KEY_FRAGMENTS = (
    "cookie",
    "set-cookie",
    "authorization",
    "auth_token",
    "access_token",
    "refresh_token",
    "session",
    "password",
    "secret",
    "api_key",
    "apikey",
    "bearer",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def assert_no_sensitive_payload(payload: Any, *, path: str = "payload") -> None:
    """Refuse cookies / tokens / headers sensibles — ne jamais les persister."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).casefold()
            if any(frag in key_l for frag in _SENSITIVE_KEY_FRAGMENTS):
                raise ValueError(f"Donnée sensible refusée dans le cache ({path}.{key}).")
            assert_no_sensitive_payload(value, path=f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            assert_no_sensitive_payload(item, path=f"{path}[{index}]")


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedStoreLocator:
    connector_key: str
    lookup_key: str
    payload: Any
    fetched_at: datetime
    expires_at: datetime
    is_fresh: bool
    is_stale: bool


@dataclass(frozen=True)
class CachedLiveOffer:
    connector_key: str
    external_store_id: str
    supplier_reference: str
    seller_code: str | None
    price_ht: Decimal | None
    price_ttc: Decimal | None
    currency: str
    stock_quantity: int | None
    stock_status: str | None
    is_salable: bool | None
    is_offer_available: bool | None
    packaging_quantity: Decimal | None
    reference_quantity: Decimal | None
    supplier_unit: str | None
    extras: dict | None
    price_fetched_at: datetime | None
    price_expires_at: datetime | None
    stock_fetched_at: datetime | None
    stock_expires_at: datetime | None
    price_fresh: bool
    stock_fresh: bool

    @property
    def fully_fresh(self) -> bool:
        return self.price_fresh and self.stock_fresh

    @property
    def has_any_data(self) -> bool:
        return self.price_ht is not None or self.price_ttc is not None or self.stock_quantity is not None


class SkuFreshness(str, Enum):
    FRESH = "fresh"  # prix + stock frais
    PRICE_FRESH_STOCK_STALE = "price_fresh_stock_stale"
    STOCK_FRESH_PRICE_STALE = "stock_fresh_price_stale"
    STALE = "stale"  # présent mais prix et stock expirés
    MISSING = "missing"


@dataclass(frozen=True)
class SkuCacheDecision:
    supplier_reference: str
    freshness: SkuFreshness
    entry: CachedLiveOffer | None
    needs_refresh: bool


@dataclass(frozen=True)
class OfferRefreshPlan:
    """Résultat de plan_offer_refresh pour un dépôt et une liste de SKU."""

    connector_key: str
    external_store_id: str
    decisions: tuple[SkuCacheDecision, ...]
    skus_to_refresh: tuple[str, ...]
    fresh_entries: dict[str, CachedLiveOffer] = field(default_factory=dict)

    @property
    def missing_skus(self) -> tuple[str, ...]:
        return tuple(d.supplier_reference for d in self.decisions if d.freshness is SkuFreshness.MISSING)


def _store_dto(row: SupplierStoreCache, *, now: datetime) -> CachedStoreLocator:
    expires = _ensure_aware(row.expires_at)
    fetched = _ensure_aware(row.fetched_at)
    fresh = expires > now
    return CachedStoreLocator(
        connector_key=row.connector_key,
        lookup_key=row.lookup_key,
        payload=row.payload,
        fetched_at=fetched,
        expires_at=expires,
        is_fresh=fresh,
        is_stale=not fresh,
    )


def _offer_dto(row: SupplierOfferCache, *, now: datetime) -> CachedLiveOffer:
    price_exp = _ensure_aware(row.price_expires_at) if row.price_expires_at else None
    stock_exp = _ensure_aware(row.stock_expires_at) if row.stock_expires_at else None
    price_fresh = bool(price_exp and price_exp > now and (row.price_ht is not None or row.price_ttc is not None))
    stock_fresh = bool(stock_exp and stock_exp > now and row.stock_quantity is not None)
    return CachedLiveOffer(
        connector_key=row.connector_key,
        external_store_id=row.external_store_id,
        supplier_reference=row.supplier_reference,
        seller_code=row.seller_code,
        price_ht=row.price_ht,
        price_ttc=row.price_ttc,
        currency=row.currency or "EUR",
        stock_quantity=row.stock_quantity,
        stock_status=row.stock_status,
        is_salable=row.is_salable,
        is_offer_available=row.is_offer_available,
        packaging_quantity=row.packaging_quantity,
        reference_quantity=row.reference_quantity,
        supplier_unit=row.supplier_unit,
        extras=row.extras,
        price_fetched_at=_ensure_aware(row.price_fetched_at) if row.price_fetched_at else None,
        price_expires_at=price_exp,
        stock_fetched_at=_ensure_aware(row.stock_fetched_at) if row.stock_fetched_at else None,
        stock_expires_at=stock_exp,
        price_fresh=price_fresh,
        stock_fresh=stock_fresh,
    )


def _negative_without_price(entry: CachedLiveOffer) -> bool:
    """Indisponibilité explicite mémorisée sans prix : réponse complète, pas un trou."""
    if entry.price_ht is not None or entry.price_ttc is not None:
        return False
    return (
        entry.stock_quantity == 0
        or entry.is_salable is False
        or entry.is_offer_available is False
    )


def _classify_offer(entry: CachedLiveOffer | None) -> SkuFreshness:
    if entry is None or not entry.has_any_data:
        return SkuFreshness.MISSING
    if _negative_without_price(entry) and entry.stock_fresh:
        return SkuFreshness.FRESH
    if entry.price_fresh and entry.stock_fresh:
        return SkuFreshness.FRESH
    if entry.price_fresh and not entry.stock_fresh:
        return SkuFreshness.PRICE_FRESH_STOCK_STALE
    if entry.stock_fresh and not entry.price_fresh:
        return SkuFreshness.STOCK_FRESH_PRICE_STALE
    return SkuFreshness.STALE


class SupplierLiveCacheService:
    """API interne cache fournisseurs — session SQLAlchemy injectée."""

    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or Settings()

    # ------------------------------------------------------------------ stores

    def get_stores(
        self,
        connector_key: str,
        lookup_key: str,
        *,
        allow_stale: bool = False,
        now: datetime | None = None,
    ) -> CachedStoreLocator | None:
        now = _ensure_aware(now or utc_now())
        row = self._get_store_row(connector_key, lookup_key)
        if row is None:
            return None
        dto = _store_dto(row, now=now)
        if dto.is_fresh or allow_stale:
            return dto
        return None

    def put_stores(
        self,
        connector_key: str,
        lookup_key: str,
        payload: Any,
        *,
        now: datetime | None = None,
        ttl_s: int | None = None,
    ) -> CachedStoreLocator:
        assert_no_sensitive_payload(payload)
        if payload is None:
            raise ValueError("payload magasins vide refusé.")
        now = _ensure_aware(now or utc_now())
        ttl = int(ttl_s if ttl_s is not None else self.settings.supplier_store_cache_ttl_s)
        expires = now + timedelta(seconds=ttl)
        row = self._get_store_row(connector_key, lookup_key)
        if row is None:
            row = SupplierStoreCache(
                connector_key=connector_key,
                lookup_key=str(lookup_key),
                payload=payload,
                fetched_at=now,
                expires_at=expires,
                refresh_lease_until=None,
            )
            self.session.add(row)
        else:
            row.payload = payload
            row.fetched_at = now
            row.expires_at = expires
            row.refresh_lease_until = None
        self.session.flush()
        return _store_dto(row, now=now)

    def try_acquire_store_refresh_lease(
        self,
        connector_key: str,
        lookup_key: str,
        *,
        now: datetime | None = None,
        lease_s: int | None = None,
    ) -> bool:
        """True = cet appelant doit interroger le fournisseur (anti-stampede simple)."""
        now = _ensure_aware(now or utc_now())
        lease = int(lease_s if lease_s is not None else self.settings.supplier_cache_refresh_lease_s)
        lease_until = now + timedelta(seconds=lease)
        row = self._get_store_row(connector_key, lookup_key)
        if row is None:
            with self.session.begin_nested():
                try:
                    self.session.add(
                        SupplierStoreCache(
                            connector_key=connector_key,
                            lookup_key=str(lookup_key),
                            payload=[],
                            fetched_at=now,
                            expires_at=now,  # placeholder expiré jusqu'au put réel
                            refresh_lease_until=lease_until,
                        )
                    )
                    self.session.flush()
                    return True
                except IntegrityError:
                    pass
            row = self._get_store_row(connector_key, lookup_key)
            if row is None:
                return True
        current_lease = _ensure_aware(row.refresh_lease_until) if row.refresh_lease_until else None
        if current_lease and current_lease > now:
            return False
        row.refresh_lease_until = lease_until
        self.session.flush()
        return True

    def release_store_refresh_lease(self, connector_key: str, lookup_key: str) -> None:
        row = self._get_store_row(connector_key, lookup_key)
        if row is not None:
            row.refresh_lease_until = None
            self.session.flush()

    # ------------------------------------------------------------------ offers

    def get_offer(
        self,
        connector_key: str,
        external_store_id: str,
        supplier_reference: str,
        *,
        allow_stale: bool = False,
        now: datetime | None = None,
    ) -> CachedLiveOffer | None:
        """Lit une offre.

        Par défaut : retourne seulement si prix OU stock encore frais
        (partial hit possible). `allow_stale=True` : stale-if-error explicite.
        """
        now = _ensure_aware(now or utc_now())
        row = self._get_offer_row(connector_key, external_store_id, supplier_reference)
        if row is None:
            return None
        dto = _offer_dto(row, now=now)
        if not dto.has_any_data:
            return None
        if dto.price_fresh or dto.stock_fresh or allow_stale:
            return dto
        return None

    def put_offer(
        self,
        connector_key: str,
        external_store_id: str,
        supplier_reference: str,
        *,
        seller_code: str | None = None,
        price_ht: Decimal | None = None,
        price_ttc: Decimal | None = None,
        currency: str = "EUR",
        stock_quantity: int | None = None,
        stock_status: str | None = None,
        is_salable: bool | None = None,
        is_offer_available: bool | None = None,
        packaging_quantity: Decimal | None = None,
        reference_quantity: Decimal | None = None,
        supplier_unit: str | None = None,
        extras: dict | None = None,
        now: datetime | None = None,
        price_ttl_s: int | None = None,
        stock_ttl_s: int | None = None,
        update_price: bool = True,
        update_stock: bool = True,
    ) -> CachedLiveOffer:
        """Écrit / met à jour une offre. Refuse d'écraser avec une réponse sans prix ni stock."""
        if extras is not None:
            assert_no_sensitive_payload(extras, path="extras")
        if update_price and price_ht is None and price_ttc is None:
            raise ValueError("Refuse d'écrire une offre sans prix HT/TTC explicite.")
        if update_stock and stock_quantity is None and not update_price:
            raise ValueError("Refuse d'écrire un refresh stock sans stock_quantity.")

        now = _ensure_aware(now or utc_now())
        price_ttl = int(price_ttl_s if price_ttl_s is not None else self.settings.supplier_offer_price_ttl_s)
        stock_ttl = int(stock_ttl_s if stock_ttl_s is not None else self.settings.supplier_offer_stock_ttl_s)

        row = self._get_offer_row(connector_key, external_store_id, supplier_reference)
        if row is None:
            with self.session.begin_nested():
                try:
                    row = SupplierOfferCache(
                        connector_key=connector_key,
                        external_store_id=str(external_store_id),
                        supplier_reference=str(supplier_reference),
                        currency=currency or "EUR",
                    )
                    self.session.add(row)
                    self.session.flush()
                except IntegrityError:
                    pass
            row = self._get_offer_row(connector_key, external_store_id, supplier_reference)
            if row is None:
                raise RuntimeError("Impossible d'upsert l'entrée cache offre.")

        row.seller_code = seller_code if seller_code is not None else row.seller_code
        row.currency = currency or row.currency or "EUR"
        row.packaging_quantity = (
            packaging_quantity if packaging_quantity is not None else row.packaging_quantity
        )
        row.reference_quantity = (
            reference_quantity if reference_quantity is not None else row.reference_quantity
        )
        row.supplier_unit = supplier_unit if supplier_unit is not None else row.supplier_unit
        if extras is not None:
            row.extras = extras

        if update_price:
            row.price_ht = price_ht
            row.price_ttc = price_ttc
            row.price_fetched_at = now
            row.price_expires_at = now + timedelta(seconds=price_ttl)

        if update_stock:
            row.stock_quantity = stock_quantity
            row.stock_status = stock_status
            row.is_salable = is_salable
            row.is_offer_available = is_offer_available
            row.stock_fetched_at = now
            row.stock_expires_at = now + timedelta(seconds=stock_ttl)

        row.refresh_lease_until = None
        self.session.flush()
        return _offer_dto(row, now=now)

    def plan_offer_refresh(
        self,
        connector_key: str,
        external_store_id: str,
        supplier_references: list[str],
        *,
        now: datetime | None = None,
    ) -> OfferRefreshPlan:
        """Identifie les SKU à rafraîchir (miss / stock expiré / prix expiré).

        Une seule liste `skus_to_refresh` pour une future requête multi-SKU.
        """
        now = _ensure_aware(now or utc_now())
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in supplier_references:
            sku = str(raw).strip()
            if not sku or sku in seen:
                continue
            seen.add(sku)
            ordered.append(sku)

        decisions: list[SkuCacheDecision] = []
        to_refresh: list[str] = []
        fresh: dict[str, CachedLiveOffer] = {}
        for sku in ordered:
            row = self._get_offer_row(connector_key, external_store_id, sku)
            entry = _offer_dto(row, now=now) if row and (
                row.price_ht is not None or row.price_ttc is not None or row.stock_quantity is not None
            ) else None
            freshness = _classify_offer(entry)
            needs = freshness is not SkuFreshness.FRESH
            if needs:
                to_refresh.append(sku)
            if entry is not None and freshness is SkuFreshness.FRESH:
                fresh[sku] = entry
            elif entry is not None and entry.price_fresh:
                # Prix encore utilisable pendant refresh stock.
                fresh[sku] = entry
            decisions.append(
                SkuCacheDecision(
                    supplier_reference=sku,
                    freshness=freshness,
                    entry=entry,
                    needs_refresh=needs,
                )
            )
        return OfferRefreshPlan(
            connector_key=connector_key,
            external_store_id=str(external_store_id),
            decisions=tuple(decisions),
            skus_to_refresh=tuple(to_refresh),
            fresh_entries=fresh,
        )

    def try_acquire_offer_refresh_lease(
        self,
        connector_key: str,
        external_store_id: str,
        supplier_reference: str,
        *,
        now: datetime | None = None,
        lease_s: int | None = None,
    ) -> bool:
        now = _ensure_aware(now or utc_now())
        lease = int(lease_s if lease_s is not None else self.settings.supplier_cache_refresh_lease_s)
        lease_until = now + timedelta(seconds=lease)
        row = self._get_offer_row(connector_key, external_store_id, supplier_reference)
        if row is None:
            with self.session.begin_nested():
                try:
                    self.session.add(
                        SupplierOfferCache(
                            connector_key=connector_key,
                            external_store_id=str(external_store_id),
                            supplier_reference=str(supplier_reference),
                            currency="EUR",
                            refresh_lease_until=lease_until,
                        )
                    )
                    self.session.flush()
                    return True
                except IntegrityError:
                    pass
            row = self._get_offer_row(connector_key, external_store_id, supplier_reference)
            if row is None:
                return True
        current_lease = _ensure_aware(row.refresh_lease_until) if row.refresh_lease_until else None
        if current_lease and current_lease > now:
            return False
        row.refresh_lease_until = lease_until
        self.session.flush()
        return True

    def release_offer_refresh_lease(
        self,
        connector_key: str,
        external_store_id: str,
        supplier_reference: str,
    ) -> None:
        row = self._get_offer_row(connector_key, external_store_id, supplier_reference)
        if row is not None:
            row.refresh_lease_until = None
            self.session.flush()

    def try_acquire_offer_refresh_leases(
        self,
        connector_key: str,
        external_store_id: str,
        supplier_references: list[str],
        *,
        now: datetime | None = None,
        lease_s: int | None = None,
    ) -> tuple[str, ...]:
        """Retourne les SKU pour lesquels ce worker a obtenu le lease."""
        won: list[str] = []
        for sku in supplier_references:
            if self.try_acquire_offer_refresh_lease(
                connector_key,
                external_store_id,
                sku,
                now=now,
                lease_s=lease_s,
            ):
                won.append(str(sku).strip())
        return tuple(won)

    # ------------------------------------------------------------------ internals

    def _get_store_row(self, connector_key: str, lookup_key: str) -> SupplierStoreCache | None:
        return self.session.scalar(
            select(SupplierStoreCache).where(
                SupplierStoreCache.connector_key == connector_key,
                SupplierStoreCache.lookup_key == str(lookup_key),
            )
        )

    def _get_offer_row(
        self, connector_key: str, external_store_id: str, supplier_reference: str
    ) -> SupplierOfferCache | None:
        return self.session.scalar(
            select(SupplierOfferCache).where(
                SupplierOfferCache.connector_key == connector_key,
                SupplierOfferCache.external_store_id == str(external_store_id),
                SupplierOfferCache.supplier_reference == str(supplier_reference),
            )
        )
