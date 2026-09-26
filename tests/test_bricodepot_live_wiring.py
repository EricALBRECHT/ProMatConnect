"""Branchement Brico LIVE + cache — tests offline (transport mocké)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.bricodepot.client import (
    PRODUCT_SKU_PAGE_SIZE,
    BricoDepotClient,
)
from app.connectors.bricodepot.connector import (
    CACHE_CONNECTOR_KEY,
    BricoDepotConnector,
    chunk_skus,
    retailer_to_dict,
)
from app.connectors.registry import build_connectors, build_live_connectors
from app.database import Base
from app.models import Agency, Offer, Product, Supplier, SupplierProduct
from app.models.supplier_live_cache import SupplierOfferCache, SupplierStoreCache
from app.schemas.location import ResolvedOrigin
from app.services.supplier_live_cache import SupplierLiveCacheService, utc_now


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    import app.models  # noqa: F401

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def settings_off():
    return Settings(bricodepot_live_enabled=False, bricodepot_max_stores=3)


@pytest.fixture
def settings_on():
    return Settings(
        bricodepot_live_enabled=True,
        bricodepot_max_stores=3,
        supplier_offer_price_ttl_s=3600,
        supplier_offer_stock_ttl_s=600,
        supplier_store_cache_ttl_s=7 * 24 * 3600,
    )


def _origin(citycode: str | None = "80021") -> ResolvedOrigin:
    return ResolvedOrigin(
        type="site",
        label="Chantier",
        source="geopf",
        latitude=49.89,
        longitude=2.30,
        citycode=citycode,
        city="Amiens",
        postcode="80000",
    )


def _retailer_payload(entity_id: int = 10, distance: float = 1.0, **extra) -> dict:
    return {
        "entity_id": entity_id,
        "seller_code": extra.get("seller_code", "2350"),
        "name": extra.get("name", "AMIENS"),
        "address": "ZI",
        "city": "AMIENS",
        "postcode": "80000",
        "latitude": 49.9,
        "longitude": 2.3,
        "phone": None,
        "distance_km": distance,
    }


def _stores_http_body(retailers: list[dict]) -> dict:
    items = []
    for r in retailers:
        items.append(
            {
                "entity_id": r["entity_id"],
                "seller_code": r["seller_code"],
                "name": r["name"],
                "address": r["address"],
                "contact_phone": None,
                "distance_from_location": r["distance_km"],
                "address_data": {
                    "city": r["city"],
                    "postcode": r["postcode"],
                    "coordinates": {
                        "latitude": r["latitude"],
                        "longitude": r["longitude"],
                    },
                },
            }
        )
    return {"data": {"retailers": {"items": items, "total_count": len(items)}}}


def _product_http_body(offers: list[dict]) -> dict:
    items = []
    for o in offers:
        items.append(
            {
                "sku": o["sku"],
                "name": o.get("name", "x"),
                "stock_quantity": o.get("stock", 10),
                "stock_status": "IN_STOCK",
                "is_salable": o.get("is_salable", True),
                "is_offer_available": o.get("is_offer_available", True),
                "price_range": {
                    "minimum_price_excluding_tax": {
                        "final_price": {
                            "value": o.get("ht"),
                            "currency": "EUR",
                        }
                    },
                    "minimum_price_including_tax": {
                        "final_price": {
                            "value": o.get("ttc"),
                            "currency": "EUR",
                        }
                    },
                },
            }
        )
    return {"data": {"products": {"items": items}}}


class RecordingTransport:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, *, url: str, payload, headers, timeout):
        self.calls.append(
            {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
        )
        if not self.responses:
            raise AssertionError("plus de réponses mock")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            return item
        return 200, item

def _seed_mapping(session: Session, *, sku: str, product_id: int = 1) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        product = Product(
            id=product_id,
            code=f"PMC{product_id:04d}",
            name=f"P{product_id}",
            category="Test",
            reference_unit="piece",
        )
        session.add(product)
        session.flush()
    supplier = session.scalar(select(Supplier).where(Supplier.name == "BRICO DEPOT"))
    if supplier is None:
        supplier = Supplier(name="BRICO DEPOT", source_type="api")
        session.add(supplier)
        session.flush()
    existing = session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier.id,
            SupplierProduct.supplier_reference == sku,
        )
    )
    if existing is None:
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                product_id=product.id,
                supplier_reference=sku,
                designation=sku,
                supplier_unit="piece",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                reference_unit="piece",
                active=True,
            )
        )
    session.commit()
    return product


def test_flag_false_no_live_connector(session, settings_off):
    assert build_live_connectors(
        session, resolved_origin=_origin(), settings=settings_off
    ) == []


def test_citycode_absent_no_live_connector(session, settings_on):
    assert (
        build_live_connectors(
            session, resolved_origin=_origin(citycode=None), settings=settings_on
        )
        == []
    )
    assert (
        build_live_connectors(
            session, resolved_origin=_origin(citycode="  "), settings=settings_on
        )
        == []
    )


def test_flag_false_build_connectors_unchanged(session, settings_off):
    keys = [c.connector_key for c in build_connectors(session)]
    assert all(not k.startswith("api:BRICO") for k in keys)
    live = build_live_connectors(
        session, resolved_origin=_origin(), settings=settings_off
    )
    assert live == []


def test_store_cache_hit_skips_http(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(
        CACHE_CONNECTOR_KEY,
        "80021",
        [_retailer_payload()],
    )
    session.commit()
    transport = RecordingTransport(
        [
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": 7.75, "ttc": 9.30, "stock": 50}]
                ),
            )
        ]
    )
    client = BricoDepotClient(transport=transport)
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=client,
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1])
    assert len(offers) == 1
    assert offers[0].agency.agency_key == "bricodepot:10"
    # Un seul appel HTTP = produits (pas de store locator)
    assert len(transport.calls) == 1
    assert "sellerId=10" in transport.calls[0]["headers"].get("X-BricoDepot-Context", "")


def test_store_cache_miss_then_hit(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    transport = RecordingTransport(
        [
            (200, _stores_http_body([_retailer_payload()])),
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": 7.75, "ttc": 9.30, "stock": 50}]
                ),
            ),
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": 7.75, "ttc": 9.30, "stock": 50}]
                ),
            ),
        ]
    )
    client = BricoDepotClient(transport=transport)
    cache = SupplierLiveCacheService(session, settings_on)
    connector = BricoDepotConnector(
        session, insee_code="80021", client=client, cache=cache, settings=settings_on
    )
    assert connector.get_offers([1])
    assert len(transport.calls) == 2  # stores + products
    # Second call: store HIT → products only if offer also hit... offer was cached
    connector.get_offers([1])
    assert len(transport.calls) == 2  # aucun nouvel appel


def test_offer_fresh_no_product_http(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3334160524579",
        seller_code="2350",
        price_ht=Decimal("7.75"),
        price_ttc=Decimal("9.30"),
        stock_quantity=50,
        is_salable=True,
        is_offer_available=True,
        stock_status="IN_STOCK",
    )
    session.commit()
    transport = RecordingTransport([])
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1])
    assert len(offers) == 1
    assert offers[0].price == Decimal("7.75")
    assert transport.calls == []


def test_stock_stale_triggers_refresh(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    now = utc_now()
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()], now=now)
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3334160524579",
        price_ht=Decimal("7.75"),
        price_ttc=Decimal("9.30"),
        stock_quantity=50,
        is_salable=True,
        is_offer_available=True,
        now=now,
        price_ttl_s=3600,
        stock_ttl_s=60,
    )
    session.commit()
    # Stock expiré, prix encore frais.
    row = session.scalar(select(SupplierOfferCache))
    row.stock_expires_at = now - timedelta(seconds=1)
    session.commit()

    transport = RecordingTransport(
        [
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": 7.75, "ttc": 9.30, "stock": 40}]
                ),
            )
        ]
    )
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1])
    assert len(transport.calls) == 1
    assert offers[0].stock == 40


def test_price_stale_triggers_refresh(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    now = utc_now()
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()], now=now)
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3334160524579",
        price_ht=Decimal("7.75"),
        price_ttc=Decimal("9.30"),
        stock_quantity=50,
        is_salable=True,
        is_offer_available=True,
        now=now,
        price_ttl_s=60,
        stock_ttl_s=3600,
    )
    session.commit()
    row = session.scalar(select(SupplierOfferCache))
    row.price_expires_at = now - timedelta(seconds=1)
    session.commit()

    transport = RecordingTransport(
        [
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": 8.10, "ttc": 9.72, "stock": 50}]
                ),
            )
        ]
    )
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1])
    assert len(transport.calls) == 1
    assert offers[0].price == Decimal("8.10")


def test_sku_absent_in_response_no_false_association(session, settings_on):
    _seed_mapping(session, sku="AAA", product_id=1)
    _seed_mapping(session, sku="BBB", product_id=2)
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    session.commit()
    # Seul BBB revient — AAA ne doit pas être associé à tort
    transport = RecordingTransport(
        [
            (
                200,
                _product_http_body(
                    [{"sku": "BBB", "ht": 32.08, "ttc": 38.50, "stock": 65}]
                ),
            )
        ]
    )
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1, 2])
    assert len(offers) == 1
    assert offers[0].supplier_reference == "BBB"
    assert offers[0].price == Decimal("32.08")


def test_missing_sku_refresh(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    session.commit()
    transport = RecordingTransport(
        [
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": 7.75, "ttc": 9.30, "stock": 50}]
                ),
            )
        ]
    )
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    assert connector.get_offers([1])
    assert len(transport.calls) == 1


def test_batching_over_10_skus(session, settings_on):
    skus = [f"SKU{i:03d}" for i in range(23)]
    for i, sku in enumerate(skus, start=1):
        _seed_mapping(session, sku=sku, product_id=i)
    assert len(chunk_skus(skus)) == 3
    assert [len(b) for b in chunk_skus(skus)] == [10, 10, 3]

    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    session.commit()

    responses = []
    for batch in chunk_skus(skus):
        responses.append(
            (
                200,
                _product_http_body(
                    [{"sku": s, "ht": 1.0, "ttc": 1.2, "stock": 5} for s in batch]
                ),
            )
        )
    transport = RecordingTransport(responses)
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers(list(range(1, 24)))
    assert len(transport.calls) == 3
    for call in transport.calls:
        variables = call["payload"][0]["queryVariables"]
        assert variables["pageSize"] <= PRODUCT_SKU_PAGE_SIZE
        assert len(variables["filter"]["sku"]["in"]) <= PRODUCT_SKU_PAGE_SIZE
    assert len(offers) == 23


def test_response_reordered_matched_by_sku(session, settings_on):
    _seed_mapping(session, sku="AAA", product_id=1)
    _seed_mapping(session, sku="BBB", product_id=2)
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    session.commit()
    # Réponse inversée
    transport = RecordingTransport(
        [
            (
                200,
                _product_http_body(
                    [
                        {"sku": "BBB", "ht": 32.08, "ttc": 38.50, "stock": 65},
                        {"sku": "AAA", "ht": 7.75, "ttc": 9.30, "stock": 50},
                    ]
                ),
            )
        ]
    )
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1, 2])
    by_ref = {o.supplier_reference: o for o in offers}
    assert by_ref["AAA"].price == Decimal("7.75")
    assert by_ref["BBB"].price == Decimal("32.08")


def test_invalid_response_does_not_overwrite_good_cache(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    now = utc_now()
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()], now=now)
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3334160524579",
        price_ht=Decimal("7.75"),
        price_ttc=Decimal("9.30"),
        stock_quantity=50,
        is_salable=True,
        is_offer_available=True,
        now=now,
        price_ttl_s=60,
        stock_ttl_s=60,
    )
    session.commit()
    row = session.scalar(select(SupplierOfferCache))
    row.price_expires_at = now - timedelta(seconds=1)
    row.stock_expires_at = now - timedelta(seconds=1)
    session.commit()

    # Réponse sans HT → ne doit pas écraser
    transport = RecordingTransport(
        [
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": None, "ttc": 9.30, "stock": 1}]
                ),
            )
        ]
    )
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1])
    assert offers[0].price == Decimal("7.75")
    assert offers[0].stock == 50
    row = session.scalar(select(SupplierOfferCache))
    assert row.price_ht == Decimal("7.75")


def test_agency_key_and_db_collision_keys(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload(entity_id=10)])
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3334160524579",
        price_ht=Decimal("7.75"),
        price_ttc=Decimal("9.30"),
        stock_quantity=50,
        is_salable=True,
        is_offer_available=True,
    )
    session.commit()
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=RecordingTransport([])),
        cache=cache,
        settings=settings_on,
    )
    offers = connector.get_offers([1])
    assert offers[0].agency.id == 10
    assert offers[0].agency.agency_key == "bricodepot:10"
    assert offers[0].agency.agency_key != "db:10"


def test_brico_error_returns_empty_soft_fail(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    from app.connectors.bricodepot.client import BricoDepotHttpError

    transport = RecordingTransport([BricoDepotHttpError(500, "boom")])
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        settings=settings_on,
    )
    assert connector.get_offers([1]) == []


def test_multiple_stores_distinct_offers(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(
        CACHE_CONNECTOR_KEY,
        "80021",
        [
            _retailer_payload(entity_id=10, distance=1.0, seller_code="2350", name="A"),
            _retailer_payload(entity_id=133, distance=2.0, seller_code="2362", name="B"),
        ],
    )
    for eid, stock in ((10, 50), (133, 12)):
        cache.put_offer(
            CACHE_CONNECTOR_KEY,
            str(eid),
            "3334160524579",
            price_ht=Decimal("7.75"),
            price_ttc=Decimal("9.30"),
            stock_quantity=stock,
            is_salable=True,
            is_offer_available=True,
        )
    session.commit()
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=RecordingTransport([])),
        cache=cache,
        settings=settings_on,
        max_stores=3,
    )
    offers = connector.get_offers([1])
    assert {o.agency.agency_key for o in offers} == {"bricodepot:10", "bricodepot:133"}
    by_key = {o.agency.agency_key: o for o in offers}
    assert by_key["bricodepot:10"].stock == 50
    assert by_key["bricodepot:133"].stock == 12


def test_unmapped_product_never_queried(session, settings_on):
    cache = SupplierLiveCacheService(session, settings_on)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    session.commit()
    transport = RecordingTransport([])
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        cache=cache,
        settings=settings_on,
    )
    assert connector.get_offers([999]) == []
    assert transport.calls == []


def test_no_writes_to_offer_agency_product(session, settings_on):
    _seed_mapping(session, sku="3334160524579")
    offers_before = session.scalars(select(Offer)).all()
    agencies_before = session.scalars(select(Agency)).all()
    products_before = session.scalars(select(Product)).all()
    transport = RecordingTransport(
        [
            (200, _stores_http_body([_retailer_payload()])),
            (
                200,
                _product_http_body(
                    [{"sku": "3334160524579", "ht": 7.75, "ttc": 9.30, "stock": 50}]
                ),
            ),
        ]
    )
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=transport),
        settings=settings_on,
    )
    assert connector.get_offers([1])
    session.commit()
    assert len(session.scalars(select(Offer)).all()) == len(offers_before)
    assert len(session.scalars(select(Agency)).all()) == len(agencies_before)
    assert len(session.scalars(select(Product)).all()) == len(products_before)
    assert session.scalar(select(SupplierStoreCache)) is not None
    assert session.scalar(select(SupplierOfferCache)) is not None


def test_live_connector_factory_injects_client(session, settings_on):
    transport = RecordingTransport([])
    client = BricoDepotClient(transport=transport)
    connectors = build_live_connectors(
        session,
        resolved_origin=_origin(),
        settings=settings_on,
        brico_client=client,
    )
    assert len(connectors) == 1
    assert connectors[0].connector_key == "api:BRICO_DEPOT"
    assert connectors[0].insee_code == "80021"
