"""Cache persistant des offres live Brico : hit, dépôt, TTL, stock 0, force."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.bricodepot.client import BricoDepotClient, BricoDepotClientError
from app.connectors.bricodepot.connector import CACHE_CONNECTOR_KEY, BricoDepotConnector
from app.database import Base
from app.models.supplier_live_cache import SupplierOfferCache
from app.services.supplier_live_cache import SupplierLiveCacheService, utc_now
from tests.test_bricodepot_live_wiring import (
    RecordingTransport,
    _product_http_body,
    _retailer_payload,
    _seed_mapping,
)

SKU = "3334160524579"


def test_brico_live_cache_ttl_default():
    assert Settings.model_fields["brico_live_cache_ttl_seconds"].default == 1800


def test_brico_live_offer_cache_hit_miss_stale():
    engine = create_engine("sqlite:///:memory:")
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    settings = Settings(
        bricodepot_live_enabled=True,
        brico_live_cache_ttl_seconds=1800,
        supplier_store_cache_ttl_s=7 * 24 * 3600,
    )
    with Session(engine) as session:
        _seed_mapping(session, sku=SKU)
        cache = SupplierLiveCacheService(session, settings)
        cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload(entity_id=10)])
        session.commit()

        def offer(stock: int, ht: str = "7.75", *, salable: bool = True):
            return (
                200,
                _product_http_body(
                    [
                        {
                            "sku": SKU,
                            "ht": ht,
                            "ttc": "9.30",
                            "stock": stock,
                            "is_salable": salable,
                            "is_offer_available": salable,
                        }
                    ]
                ),
            )

        transport = RecordingTransport([offer(50)])
        client = BricoDepotClient(transport=transport)
        connector = BricoDepotConnector(
            session,
            insee_code="80021",
            client=client,
            cache=cache,
            settings=settings,
        )

        first = connector.get_offers([1])
        assert len(transport.calls) == 1
        assert len(first) == 1
        assert first[0].live_status == "live"
        assert first[0].fetched_at is not None
        assert first[0].price == Decimal("7.75")
        assert _rows(session) == 1
        row = _row(session)
        assert _seconds(row.price_expires_at, row.price_fetched_at) == 1800
        assert _seconds(row.stock_expires_at, row.stock_fetched_at) == 1800

        second = connector.get_offers([1])
        assert len(transport.calls) == 1
        assert second[0].live_status == "cache"
        assert second[0].price == Decimal("7.75")
        assert _rows(session) == 1

        cache.put_stores(
            CACHE_CONNECTOR_KEY,
            "80021",
            [_retailer_payload(entity_id=10), _retailer_payload(entity_id=20, seller_code="9999")],
        )
        session.commit()
        transport.responses.append(offer(12, ht="8.10"))
        third = connector.get_offers([1])
        assert len(transport.calls) == 2
        assert _rows(session) == 2
        assert any(o.agency.external_id == "20" or "20" in (o.agency.agency_key or "") for o in third)

        aged = _row(session, store="10")
        aged.price_expires_at = utc_now() - timedelta(seconds=5)
        aged.stock_expires_at = utc_now() - timedelta(seconds=5)
        session.commit()
        transport.responses.append(offer(4, ht="8.50"))
        refreshed = connector.get_offers([1])
        assert len(transport.calls) == 3
        depot10 = next(o for o in refreshed if (o.agency.agency_key or "").endswith(":10"))
        assert depot10.live_status == "live"
        assert depot10.price == Decimal("8.50")
        assert _rows(session) == 2

        aged = _row(session, store="10")
        aged.price_expires_at = utc_now() - timedelta(seconds=5)
        aged.stock_expires_at = utc_now() - timedelta(seconds=5)
        session.commit()
        transport.responses.append(offer(0, salable=False))
        zero = connector.get_offers([1])
        assert len(transport.calls) == 4
        zero10 = next(o for o in zero if (o.agency.agency_key or "").endswith(":10"))
        assert zero10.stock == 0
        assert _row(session, store="10").stock_quantity == 0
        cached_zero = connector.get_offers([1])
        assert len(transport.calls) == 4
        assert cached_zero
        assert all(
            o.live_status == "cache"
            for o in cached_zero
            if (o.agency.agency_key or "").endswith(":10")
        )

        transport.responses.append(offer(3, ht="9.00"))
        transport.responses.append(offer(3, ht="9.00"))
        forced = connector.get_offers([1], force_refresh=True)
        assert len(transport.calls) == 6
        assert any(o.live_status == "live" and o.price == Decimal("9.00") for o in forced)
        assert _rows(session) == 2

        before = len(transport.calls)
        again = connector.get_offers([1])
        assert len(transport.calls) == before
        assert again
        assert _rows(session) == 2

        aged = _row(session, store="10")
        kept = aged.price_ht
        aged.price_expires_at = utc_now() - timedelta(seconds=5)
        aged.stock_expires_at = utc_now() - timedelta(seconds=5)
        session.commit()
        transport.responses.append(BricoDepotClientError("temporaire"))
        stale = connector.get_offers([1])
        stale10 = next(o for o in stale if (o.agency.agency_key or "").endswith(":10"))
        assert stale10.live_status == "stale"
        assert stale10.price == kept
        assert _row(session, store="10").price_ht == kept
        assert _rows(session) == 2


def _rows(session: Session, sku: str = SKU) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(SupplierOfferCache)
            .where(SupplierOfferCache.supplier_reference == sku)
        )
        or 0
    )


def _row(session: Session, store: str = "10", sku: str = SKU) -> SupplierOfferCache:
    row = session.scalar(
        select(SupplierOfferCache).where(
            SupplierOfferCache.external_store_id == store,
            SupplierOfferCache.supplier_reference == sku,
        )
    )
    assert row is not None
    return row


def _seconds(expires, fetched) -> int:
    return int((expires - fetched).total_seconds())
