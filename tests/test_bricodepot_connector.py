"""Tests offline BricoDepotClient / BricoDepotConnector — transport mocké, aucune écriture DB."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from app.connectors.bricodepot.client import (
    BricoDepotClient,
    BricoDepotClientError,
    BricoDepotGraphQLError,
    BricoDepotHttpError,
    BricoDepotTimeoutError,
    build_product_headers,
    experimental_in_store_available,
    parse_products_by_sku,
    parse_retailers,
)
from app.connectors.bricodepot.connector import (
    BricoDepotConnector,
    SUPPLIER_NAME,
    choose_source_price,
    derive_vat_rate_from_explicit_prices,
    retailer_to_agency_data,
)
from app.connectors.bricodepot.client import BricoProductOffer, BricoMoney, BricoRetailer
from app.models import Agency, Offer, Product, Supplier, SupplierProduct


def _retailers_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"retailers": {"items": items, "total_count": len(items)}}}


def _products_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"products": {"items": items}}}


def _product_item(
    sku: str,
    *,
    ht: float | None = 7.75,
    ttc: float | None = 9.3,
    stock: int | None = 50,
    salable: bool = True,
    offer_available: bool = True,
    stock_status: str = "IN_STOCK",
) -> dict[str, Any]:
    def money(value: float | None) -> dict[str, Any]:
        return {"final_price": {"currency": "EUR", "value": value}}

    return {
        "sku": sku,
        "name": f"Product {sku}",
        "stock_status": stock_status,
        "stock_quantity": stock,
        "is_salable": salable,
        "is_offer_available": offer_available,
        "measure_price": {
            "measure_price_excluding_tax": money(2.58 if ht else None),
            "measure_price_including_tax": money(3.1 if ttc else None),
        },
        "price_range": {
            "minimum_price_excluding_tax": money(ht),
            "minimum_price_including_tax": money(ttc),
        },
    }


def _retailer_item(
    entity_id: int,
    *,
    seller_code: str = "2350",
    name: str = "AMIENS",
    lat: float | None = 49.9,
    lon: float | None = 2.3,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "seller_code": seller_code,
        "name": name,
        "address": "Zone Industrielle",
        "contact_phone": "03 00 00 00 00",
        "distance_from_location": 1.5,
        "address_data": {
            "city": name,
            "postcode": "80000",
            "street": "Zone Industrielle",
            "coordinates": {"latitude": lat, "longitude": lon},
        },
    }


class RecordingTransport:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, url, payload, headers, timeout):
        self.calls.append(
            {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
        )
        if not self.responses:
            raise BricoDepotClientError("plus de réponses mock")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            return item
        return 200, item


@pytest.fixture
def brico_supplier(session):
    existing = session.scalar(select(Supplier).where(Supplier.name == SUPPLIER_NAME))
    if existing:
        return existing
    supplier = Supplier(name=SUPPLIER_NAME, source_type="api", source_key="bricodepot")
    session.add(supplier)
    session.flush()
    return supplier


@pytest.fixture
def mapped_sp(session, brico_supplier):
    product = session.get(Product, 1)
    assert product is not None
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=brico_supplier.id,
        supplier_reference="3334160524579",
        designation="Plaque BA13",
        supplier_unit="piece",
        reference_quantity=Decimal("3"),
        packaging_quantity=Decimal("1"),
        reference_unit=product.reference_unit,
        image_url="https://example.test/img.jpg",
        active=True,
    )
    session.add(sp)
    session.commit()
    return sp


def test_parse_retailers_normal():
    retailers = parse_retailers(
        _retailers_payload([_retailer_item(10), _retailer_item(133, seller_code="2362", name="DIEPPE")])
    )
    assert len(retailers) == 2
    assert retailers[0].entity_id == 10
    assert retailers[0].seller_code == "2350"
    assert retailers[1].entity_id == 133


def test_parse_products_reordered_and_missing():
    payload = _products_payload(
        [
            _product_item("3596265336819", ht=32.08, ttc=38.5, stock=65),
            _product_item("3334160524579"),
        ]
    )
    by_sku = parse_products_by_sku(
        payload, requested_skus=["3334160524579", "3596265336819", "MISSING"]
    )
    assert set(by_sku) == {"3334160524579", "3596265336819"}
    assert by_sku["3334160524579"].price_ht_piece.value == Decimal("7.75")
    assert by_sku["3596265336819"].stock_quantity == 65


def test_product_headers_no_cookie():
    headers = build_product_headers(seller_id="10")
    assert "Cookie" not in headers
    assert "sellerId=10" in headers["X-BricoDepot-Context"]
    assert "storeId=1" in headers["X-BricoDepot-Context"]


def test_client_store_locator_and_multi_sku():
    transport = RecordingTransport(
        [
            _retailers_payload([_retailer_item(10)]),
            _products_payload(
                [
                    _product_item("B"),
                    _product_item("A", ht=1, ttc=1.2, stock=1),
                ]
            ),
        ]
    )
    client = BricoDepotClient(transport=transport, timeout_s=5)
    retailers = client.fetch_retailers("80021")
    assert len(retailers) == 1
    by_sku = client.fetch_products_by_sku(seller_id=10, skus=["A", "B"])
    assert set(by_sku) == {"A", "B"}
    # Une seule requête produit, multi-SKU
    product_call = transport.calls[1]
    assert product_call["payload"][0]["queryVariables"]["filter"]["sku"]["in"] == ["A", "B"]
    assert "Cookie" not in product_call["headers"]


def test_client_http_error():
    client = BricoDepotClient(transport=RecordingTransport([BricoDepotHttpError(500, "boom")]))
    with pytest.raises(BricoDepotHttpError):
        client.fetch_retailers("80021")


def test_client_graphql_error():
    client = BricoDepotClient(
        transport=RecordingTransport([{"errors": [{"message": "x"}], "data": None}])
    )
    with pytest.raises(BricoDepotGraphQLError):
        client.fetch_retailers("80021")


def test_client_timeout():
    client = BricoDepotClient(transport=RecordingTransport([BricoDepotTimeoutError("timeout")]))
    with pytest.raises(BricoDepotTimeoutError):
        client.fetch_retailers("80021")


def test_experimental_availability_rule():
    assert experimental_in_store_available(
        stock_quantity=50, is_salable=True, is_offer_available=True
    )
    assert not experimental_in_store_available(
        stock_quantity=0, is_salable=True, is_offer_available=True
    )
    assert not experimental_in_store_available(
        stock_quantity=5, is_salable=False, is_offer_available=True
    )
    assert not experimental_in_store_available(
        stock_quantity=5, is_salable=True, is_offer_available=False
    )


def test_choose_source_price_never_invents_from_measure():
    offer = BricoProductOffer(
        sku="X",
        name=None,
        price_ht_piece=BricoMoney(None),
        price_ttc_piece=BricoMoney(None),
        price_ht_measure=BricoMoney(Decimal("2.58")),
        price_ttc_measure=BricoMoney(Decimal("3.1")),
        stock_quantity=50,
        stock_status="IN_STOCK",
        is_salable=True,
        is_offer_available=True,
        raw_item={},
    )
    assert choose_source_price(offer) is None


def test_derive_vat_from_explicit_ht_ttc():
    rate = derive_vat_rate_from_explicit_prices(Decimal("7.75"), Decimal("9.30"))
    assert rate == Decimal("20.00")


def test_retailer_to_agency_keeps_entity_and_seller_code():
    agency = retailer_to_agency_data(
        BricoRetailer(
            entity_id=10,
            seller_code="2350",
            name="AMIENS",
            address="ZI",
            city="AMIENS",
            postcode="80000",
            latitude=49.9,
            longitude=2.3,
            phone=None,
            distance_km=1.0,
        )
    )
    assert agency.id == 10
    assert agency.external_id == "2350"
    assert agency.agency_key == "bricodepot:10"
    assert agency.is_geolocated


def test_connector_builds_offers_multi_depot_multi_sku(session, mapped_sp, brico_supplier):
    product2 = session.get(Product, 7)
    assert product2 is not None
    session.add(
        SupplierProduct(
            product_id=product2.id,
            supplier_id=brico_supplier.id,
            supplier_reference="3596265336819",
            designation="Autre",
            supplier_unit="piece",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit=product2.reference_unit,
            active=True,
        )
    )
    session.commit()

    sku1, sku2 = "3334160524579", "3596265336819"
    transport = RecordingTransport(
        [
            _retailers_payload(
                [
                    _retailer_item(10, seller_code="2350", name="AMIENS"),
                    _retailer_item(133, seller_code="2362", name="DIEPPE"),
                ]
            ),
            # dépôt 10 — réponse réordonnée
            _products_payload(
                [
                    _product_item(sku2, ht=32.08, ttc=38.5, stock=65),
                    _product_item(sku1, stock=50),
                ]
            ),
            # dépôt 133
            _products_payload(
                [
                    _product_item(sku1, stock=0, salable=False, offer_available=True),
                    _product_item(sku2, ht=32.08, ttc=38.5, stock=10),
                ]
            ),
        ]
    )
    client = BricoDepotClient(transport=transport)
    connector = BricoDepotConnector(session, insee_code="80021", client=client)
    offers = connector.get_offers([mapped_sp.product_id, product2.id])

    # 2 dépôts × jusqu'à 2 SKU, mais stock 0 / non salable → stock=0 quand même émis si prix OK
    assert all(o.supplier == SUPPLIER_NAME for o in offers)
    assert {o.agency.id for o in offers} == {10, 133}
    assert {o.supplier_reference for o in offers} == {sku1, sku2}

    # Une requête stores + une requête produits par dépôt (pas N×SKU)
    assert len(transport.calls) == 3
    for call in transport.calls[1:]:
        assert set(call["payload"][0]["queryVariables"]["filter"]["sku"]["in"]) == {
            sku1,
            sku2,
        }

    amiens_sku1 = next(
        o for o in offers if o.agency.id == 10 and o.supplier_reference == sku1
    )
    assert amiens_sku1.price == Decimal("7.75")
    assert amiens_sku1.tax_basis == "HT"
    assert amiens_sku1.vat_rate == Decimal("20.00")
    assert amiens_sku1.stock == 50

    dieppe_sku1 = next(
        o for o in offers if o.agency.id == 133 and o.supplier_reference == sku1
    )
    assert dieppe_sku1.stock == 0  # non salable / qty 0


def test_connector_sku_missing_does_not_fail_others(session, mapped_sp):
    transport = RecordingTransport(
        [
            _retailers_payload([_retailer_item(10)]),
            _products_payload([_product_item("3334160524579")]),
        ]
    )
    connector = BricoDepotConnector(
        session, insee_code="80021", client=BricoDepotClient(transport=transport)
    )
    offers = connector.get_offers([mapped_sp.product_id, 999999])
    assert len(offers) == 1
    assert offers[0].supplier_reference == "3334160524579"


def test_connector_null_price_skipped(session, mapped_sp):
    transport = RecordingTransport(
        [
            _retailers_payload([_retailer_item(10)]),
            _products_payload(
                [_product_item("3334160524579", ht=None, ttc=None, stock=50)]
            ),
        ]
    )
    connector = BricoDepotConnector(
        session, insee_code="80021", client=BricoDepotClient(transport=transport)
    )
    assert connector.get_offers([mapped_sp.product_id]) == []


def test_connector_http_error_returns_empty(session, mapped_sp):
    transport = RecordingTransport([BricoDepotHttpError(503, "down")])
    connector = BricoDepotConnector(
        session, insee_code="80021", client=BricoDepotClient(transport=transport)
    )
    assert connector.get_offers([mapped_sp.product_id]) == []


def test_connector_offer_available_false_zero_stock(session, mapped_sp):
    transport = RecordingTransport(
        [
            _retailers_payload([_retailer_item(10)]),
            _products_payload(
                [
                    _product_item(
                        "3334160524579",
                        stock=50,
                        salable=True,
                        offer_available=False,
                    )
                ]
            ),
        ]
    )
    connector = BricoDepotConnector(
        session, insee_code="80021", client=BricoDepotClient(transport=transport)
    )
    offers = connector.get_offers([mapped_sp.product_id])
    assert len(offers) == 1
    assert offers[0].stock == 0


def test_connector_no_coords_still_builds_agency(session, mapped_sp):
    transport = RecordingTransport(
        [
            _retailers_payload(
                [_retailer_item(10, lat=None, lon=None)]
            ),
            _products_payload([_product_item("3334160524579")]),
        ]
    )
    connector = BricoDepotConnector(
        session, insee_code="80021", client=BricoDepotClient(transport=transport)
    )
    offers = connector.get_offers([mapped_sp.product_id])
    assert len(offers) == 1
    assert offers[0].agency.latitude is None
    assert offers[0].agency.external_id == "2350"
    assert not offers[0].agency.is_national_catalog  # external_id présent


def test_connector_writes_no_offer_no_agency(session, mapped_sp):
    before_offers = session.scalar(select(func.count()).select_from(Offer))
    before_agencies = session.scalar(select(func.count()).select_from(Agency))
    transport = RecordingTransport(
        [
            _retailers_payload([_retailer_item(10)]),
            _products_payload([_product_item("3334160524579")]),
        ]
    )
    connector = BricoDepotConnector(
        session, insee_code="80021", client=BricoDepotClient(transport=transport)
    )
    assert connector.get_offers([mapped_sp.product_id])
    session.flush()
    assert session.scalar(select(func.count()).select_from(Offer)) == before_offers
    assert session.scalar(select(func.count()).select_from(Agency)) == before_agencies


def test_health_ok(session):
    connector = BricoDepotConnector(
        session, insee_code="80021", client=BricoDepotClient(transport=RecordingTransport([]))
    )
    health = connector.health()
    assert health.ok is True
    assert health.source_type == "api"
    assert health.connector_key.startswith("api:")


def test_not_in_build_connectors(session):
    from app.connectors.registry import build_connectors

    keys = [c.connector_key for c in build_connectors(session)]
    assert not any("BRICO" in k.upper() for k in keys)
