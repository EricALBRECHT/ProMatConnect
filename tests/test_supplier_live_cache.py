"""Tests offline — cache fournisseurs LIVE partagé (SQLite mémoire)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models.supplier_live_cache import SupplierOfferCache, SupplierStoreCache
from app.services.supplier_live_cache import (
    SkuFreshness,
    SupplierLiveCacheService,
    assert_no_sensitive_payload,
    utc_now,
)

CONNECTOR = "api:BRICO_DEPOT"
OTHER = "api:POINT_P"
SKU_A = "3334160524579"
SKU_B = "3596265336819"
SKU_C = "missing-sku"
SKU_D = "1111111111111"
STORE_10 = "10"
STORE_20 = "20"
INSEE = "80021"


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    # Enregistre les tables cache (+ Base déjà peuplé par imports modèles).
    import app.models  # noqa: F401

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def settings():
    return Settings(
        supplier_store_cache_ttl_s=7 * 24 * 3600,
        supplier_offer_price_ttl_s=3600,
        supplier_offer_stock_ttl_s=600,
        supplier_cache_refresh_lease_s=30,
    )


@pytest.fixture
def cache(session, settings):
    return SupplierLiveCacheService(session, settings)


def _put_offer(cache, *, store=STORE_10, sku=SKU_A, now=None, ht="7.75", ttc="9.30", stock=50, **kw):
    return cache.put_offer(
        CONNECTOR,
        store,
        sku,
        seller_code="2350",
        price_ht=Decimal(ht),
        price_ttc=Decimal(ttc),
        currency="EUR",
        stock_quantity=stock,
        stock_status="IN_STOCK",
        is_salable=True,
        is_offer_available=True,
        packaging_quantity=Decimal("1"),
        reference_quantity=Decimal("1"),
        supplier_unit="piece",
        now=now,
        **kw,
    )


def test_store_cache_miss_then_hit(cache, session):
    assert cache.get_stores(CONNECTOR, INSEE) is None
    payload = [{"entity_id": 10, "seller_code": "2350", "city": "Amiens"}]
    stored = cache.put_stores(CONNECTOR, INSEE, payload)
    session.commit()
    assert stored.is_fresh
    hit = cache.get_stores(CONNECTOR, INSEE)
    assert hit is not None
    assert hit.payload == payload
    assert hit.lookup_key == INSEE


def test_store_ttl_and_stale_if_error(cache, settings):
    now = utc_now()
    cache.put_stores(CONNECTOR, INSEE, [{"entity_id": 10}], now=now, ttl_s=60)
    assert cache.get_stores(CONNECTOR, INSEE, now=now + timedelta(seconds=30)) is not None
    assert cache.get_stores(CONNECTOR, INSEE, now=now + timedelta(seconds=120)) is None
    stale = cache.get_stores(
        CONNECTOR, INSEE, allow_stale=True, now=now + timedelta(seconds=120)
    )
    assert stale is not None and stale.is_stale


def test_offer_cache_miss_hit(cache, session):
    assert cache.get_offer(CONNECTOR, STORE_10, SKU_A) is None
    _put_offer(cache)
    session.commit()
    hit = cache.get_offer(CONNECTOR, STORE_10, SKU_A)
    assert hit is not None
    assert hit.price_ht == Decimal("7.75")
    assert hit.price_ttc == Decimal("9.30")
    assert hit.stock_quantity == 50
    assert hit.price_fresh and hit.stock_fresh and hit.fully_fresh


def test_price_fresh_stock_expired(cache, settings):
    now = utc_now()
    _put_offer(cache, now=now, price_ttl_s=3600, stock_ttl_s=600)
    later = now + timedelta(seconds=900)  # stock expiré, prix encore frais
    entry = cache.get_offer(CONNECTOR, STORE_10, SKU_A, now=later)
    assert entry is not None
    assert entry.price_fresh is True
    assert entry.stock_fresh is False
    assert entry.fully_fresh is False


def test_price_expired_stock_fresh(cache):
    now = utc_now()
    _put_offer(cache, now=now, price_ttl_s=60, stock_ttl_s=3600)
    later = now + timedelta(seconds=120)
    entry = cache.get_offer(CONNECTOR, STORE_10, SKU_A, now=later)
    assert entry is not None
    assert entry.price_fresh is False
    assert entry.stock_fresh is True


def test_both_expired_hidden_unless_allow_stale(cache):
    now = utc_now()
    _put_offer(cache, now=now, price_ttl_s=60, stock_ttl_s=60)
    later = now + timedelta(seconds=120)
    assert cache.get_offer(CONNECTOR, STORE_10, SKU_A, now=later) is None
    stale = cache.get_offer(CONNECTOR, STORE_10, SKU_A, allow_stale=True, now=later)
    assert stale is not None and not stale.price_fresh and not stale.stock_fresh


def test_replace_after_refresh(cache, session):
    now = utc_now()
    _put_offer(cache, now=now, ht="7.75", stock=50)
    newer = now + timedelta(minutes=5)
    _put_offer(cache, now=newer, ht="8.00", ttc="9.60", stock=40)
    session.commit()
    hit = cache.get_offer(CONNECTOR, STORE_10, SKU_A, now=newer)
    assert hit.price_ht == Decimal("8.00")
    assert hit.stock_quantity == 40


def test_isolation_stores(cache):
    _put_offer(cache, store=STORE_10, sku=SKU_A, stock=50)
    _put_offer(cache, store=STORE_20, sku=SKU_A, stock=3, ht="7.75", ttc="9.30")
    a = cache.get_offer(CONNECTOR, STORE_10, SKU_A)
    b = cache.get_offer(CONNECTOR, STORE_20, SKU_A)
    assert a.stock_quantity == 50
    assert b.stock_quantity == 3


def test_isolation_connectors(cache):
    _put_offer(cache, stock=50)
    cache.put_offer(
        OTHER,
        STORE_10,
        SKU_A,
        price_ht=Decimal("1.00"),
        price_ttc=Decimal("1.20"),
        stock_quantity=9,
        is_salable=True,
        is_offer_available=True,
    )
    assert cache.get_offer(CONNECTOR, STORE_10, SKU_A).stock_quantity == 50
    assert cache.get_offer(OTHER, STORE_10, SKU_A).stock_quantity == 9


def test_isolation_skus(cache):
    _put_offer(cache, sku=SKU_A, stock=50)
    _put_offer(cache, sku=SKU_B, stock=65, ht="32.08", ttc="38.50")
    assert cache.get_offer(CONNECTOR, STORE_10, SKU_A).stock_quantity == 50
    assert cache.get_offer(CONNECTOR, STORE_10, SKU_B).price_ht == Decimal("32.08")


def test_multi_sku_partial_plan(cache):
    now = utc_now()
    _put_offer(cache, sku=SKU_A, now=now)  # fresh
    _put_offer(cache, sku=SKU_B, now=now, price_ttl_s=3600, stock_ttl_s=60)  # stock expires soon
    _put_offer(cache, sku=SKU_D, now=now)  # fresh
    later = now + timedelta(seconds=120)
    plan = cache.plan_offer_refresh(
        CONNECTOR, STORE_10, [SKU_A, SKU_B, SKU_C, SKU_D], now=later
    )
    by_sku = {d.supplier_reference: d for d in plan.decisions}
    assert by_sku[SKU_A].freshness is SkuFreshness.FRESH
    assert by_sku[SKU_B].freshness is SkuFreshness.PRICE_FRESH_STOCK_STALE
    assert by_sku[SKU_C].freshness is SkuFreshness.MISSING
    assert by_sku[SKU_D].freshness is SkuFreshness.FRESH
    assert plan.skus_to_refresh == (SKU_B, SKU_C)
    assert SKU_A in plan.fresh_entries and SKU_D in plan.fresh_entries
    assert SKU_B in plan.fresh_entries  # prix encore utilisable


def test_timestamps_ttl_values(cache, settings):
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    _put_offer(cache, now=now)
    row = cache.session.scalar(
        select(SupplierOfferCache).where(SupplierOfferCache.supplier_reference == SKU_A)
    )
    fetched = row.price_fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    assert fetched == now
    price_exp = row.price_expires_at
    if price_exp.tzinfo is None:
        price_exp = price_exp.replace(tzinfo=timezone.utc)
    stock_exp = row.stock_expires_at
    if stock_exp.tzinfo is None:
        stock_exp = stock_exp.replace(tzinfo=timezone.utc)
    assert price_exp == now + timedelta(seconds=settings.supplier_offer_price_ttl_s)
    assert stock_exp == now + timedelta(seconds=settings.supplier_offer_stock_ttl_s)


def test_refuse_invalid_overwrite_without_price(cache):
    _put_offer(cache, ht="7.75", stock=50)
    with pytest.raises(ValueError, match="prix"):
        cache.put_offer(
            CONNECTOR,
            STORE_10,
            SKU_A,
            price_ht=None,
            price_ttc=None,
            stock_quantity=10,
            update_price=True,
            update_stock=True,
        )
    assert cache.get_offer(CONNECTOR, STORE_10, SKU_A).price_ht == Decimal("7.75")


def test_refuse_sensitive_payload():
    with pytest.raises(ValueError, match="sensible"):
        assert_no_sensitive_payload({"Cookie": "session=abc"})
    with pytest.raises(ValueError, match="sensible"):
        assert_no_sensitive_payload([{"authorization": "Bearer x"}])
    with pytest.raises(ValueError, match="sensible"):
        cache_dummy = None
        _ = cache_dummy
        assert_no_sensitive_payload({"meta": {"access_token": "x"}})


def test_put_stores_rejects_sensitive(cache):
    with pytest.raises(ValueError, match="sensible"):
        cache.put_stores(CONNECTOR, INSEE, [{"entity_id": 1, "set-cookie": "x"}])


def test_persistence_across_sessions(engine, settings):
    with Session(engine) as s1:
        SupplierLiveCacheService(s1, settings).put_stores(
            CONNECTOR, INSEE, [{"entity_id": 10}]
        )
        _put_offer(SupplierLiveCacheService(s1, settings))
        s1.commit()
    with Session(engine) as s2:
        cache = SupplierLiveCacheService(s2, settings)
        assert cache.get_stores(CONNECTOR, INSEE) is not None
        assert cache.get_offer(CONNECTOR, STORE_10, SKU_A).stock_quantity == 50
        assert s2.scalar(select(SupplierStoreCache)) is not None
        assert s2.scalar(select(SupplierOfferCache)) is not None


def test_anti_stampede_lease_second_caller_blocked(cache):
    now = utc_now()
    assert cache.try_acquire_offer_refresh_lease(CONNECTOR, STORE_10, SKU_A, now=now) is True
    assert cache.try_acquire_offer_refresh_lease(CONNECTOR, STORE_10, SKU_A, now=now) is False
    # Après put, lease libéré → nouvel acquire possible
    _put_offer(cache, now=now)
    assert cache.try_acquire_offer_refresh_lease(
        CONNECTOR, STORE_10, SKU_A, now=now + timedelta(seconds=1)
    ) is True


def test_anti_stampede_lease_expires(cache):
    now = utc_now()
    assert cache.try_acquire_offer_refresh_lease(
        CONNECTOR, STORE_10, SKU_A, now=now, lease_s=30
    )
    assert (
        cache.try_acquire_offer_refresh_lease(
            CONNECTOR, STORE_10, SKU_A, now=now + timedelta(seconds=10), lease_s=30
        )
        is False
    )
    assert (
        cache.try_acquire_offer_refresh_lease(
            CONNECTOR, STORE_10, SKU_A, now=now + timedelta(seconds=40), lease_s=30
        )
        is True
    )


def test_batch_leases(cache):
    won = cache.try_acquire_offer_refresh_leases(CONNECTOR, STORE_10, [SKU_A, SKU_B])
    assert set(won) == {SKU_A, SKU_B}
    won2 = cache.try_acquire_offer_refresh_leases(CONNECTOR, STORE_10, [SKU_A, SKU_B, SKU_C])
    assert won2 == (SKU_C,)


def test_store_lease(cache):
    now = utc_now()
    assert cache.try_acquire_store_refresh_lease(CONNECTOR, INSEE, now=now)
    assert cache.try_acquire_store_refresh_lease(CONNECTOR, INSEE, now=now) is False
    cache.put_stores(CONNECTOR, INSEE, [{"entity_id": 10}], now=now)
    assert cache.try_acquire_store_refresh_lease(
        CONNECTOR, INSEE, now=now + timedelta(seconds=1)
    )


def test_release_lease_on_error_path(cache):
    now = utc_now()
    assert cache.try_acquire_offer_refresh_lease(CONNECTOR, STORE_10, SKU_A, now=now)
    cache.release_offer_refresh_lease(CONNECTOR, STORE_10, SKU_A)
    assert cache.try_acquire_offer_refresh_lease(CONNECTOR, STORE_10, SKU_A, now=now)
