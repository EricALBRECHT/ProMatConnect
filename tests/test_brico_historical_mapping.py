"""Régression : mapping historique BRICO_DEPOT → offres live → ComparisonService."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.bricodepot.client import BricoDepotClient
from app.connectors.bricodepot.connector import (
    CACHE_CONNECTOR_KEY,
    SUPPLIER_NAME,
    SUPPLIER_NAME_ALIASES,
    BricoDepotConnector,
    is_brico_supplier_name,
)
from app.connectors.registry import build_connectors, build_live_connectors
from app.database import Base
from app.models import Product, Supplier, SupplierProduct
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.schemas.location import ResolvedOrigin
from app.services.comparison import ComparisonService
from app.services.supplier_live_cache import SupplierLiveCacheService


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


def _origin(citycode: str = "80021") -> ResolvedOrigin:
    return ResolvedOrigin(
        type="site",
        label="Amiens",
        source="geopf",
        latitude=49.89,
        longitude=2.30,
        citycode=citycode,
        city="Amiens",
        postcode="80000",
    )


def _seed_historical_brico_mapping(session: Session) -> Product:
    """Reproduit le catalogue importé : Supplier.name = BRICO_DEPOT (underscore)."""
    product = Product(
        id=25,
        code="PMC-BA13-LIGHT-2500X1200",
        name="Plaque BA13 standard légère / Purelight 2500 × 1200 × 13 mm",
        category="Plâtre",
        reference_unit="pièce",
    )
    supplier = Supplier(
        name="BRICO_DEPOT",
        source_type="file",
        source_key="import-test-brico",
    )
    session.add_all([product, supplier])
    session.flush()
    session.add(
        SupplierProduct(
            id=69,
            product_id=25,
            supplier_id=supplier.id,
            supplier_reference="3334160524579",
            designation="BA13 Purelight",
            supplier_unit="plaque",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="pièce",
            active=True,
        )
    )
    session.commit()
    return product


def _retailer_payload(entity_id: int = 10) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "seller_code": "2350",
        "name": "AMIENS",
        "address": "ZI",
        "city": "AMIENS",
        "postcode": "80000",
        "latitude": 49.9,
        "longitude": 2.3,
        "phone": None,
        "distance_km": 1.0,
    }


class EmptyTransport:
    def __call__(self, **kwargs):
        raise AssertionError("aucun appel HTTP attendu (cache HIT)")


def test_aliases_cover_historical_and_live_names():
    assert is_brico_supplier_name("BRICO_DEPOT")
    assert is_brico_supplier_name("BRICO DEPOT")
    assert not is_brico_supplier_name("POINT.P")
    assert SUPPLIER_NAME_ALIASES == {"BRICO DEPOT", "BRICO_DEPOT"}


def test_load_mapped_products_accepts_brico_underscore(session):
    _seed_historical_brico_mapping(session)
    connector = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=EmptyTransport()),
        settings=Settings(bricodepot_live_enabled=True),
    )
    mapped = connector._load_mapped_products([25])
    assert len(mapped) == 1
    sp, product = mapped[0]
    assert product.id == 25
    assert sp.id == 69
    assert sp.supplier_reference == "3334160524579"


def test_live_offers_reach_comparison_with_historical_supplier(session):
    _seed_historical_brico_mapping(session)
    settings = Settings(
        bricodepot_live_enabled=True,
        bricodepot_max_stores=3,
        supplier_offer_price_ttl_s=3600,
        supplier_offer_stock_ttl_s=600,
    )
    cache = SupplierLiveCacheService(session, settings)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload(10)])
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

    live = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=EmptyTransport()),
        cache=cache,
        settings=settings,
    )
    offers = live.get_offers([25])
    assert len(offers) >= 1
    offer = offers[0]
    assert offer.product_id == 25
    assert offer.supplier == SUPPLIER_NAME
    assert offer.supplier_reference == "3334160524579"
    assert offer.agency.id == 10
    assert offer.agency.agency_key == "bricodepot:10"
    assert offer.price == Decimal("7.75")
    assert offer.tax_basis == "HT"
    assert offer.vat_rate == Decimal("20.00")
    assert offer.stock == 50
    assert offer.available_quantity == Decimal("50")
    assert offer.covers_packs(1) is True
    assert offer.reference_unit == "pièce"
    assert offer.packaging_quantity == Decimal("1")
    assert offer.preparation_minutes == 0

    # Collision identité : DB id=10 ≠ live agency_key
    db_connectors = build_connectors(session)
    assert any(c.supplier_name == "BRICO_DEPOT" for c in db_connectors)

    products = {
        25: ProductRead.model_validate(session.get(Product, 25)),
    }
    response = ComparisonService(
        [*db_connectors, live],
        49.89,
        2.30,
        origin=_origin(),
    ).compare([CartLine(product_id=25, quantity=Decimal("1"))], products)

    live_option = next(
        o for o in response.options if o.title == f"Tout chez {SUPPLIER_NAME}"
    )
    assert live_option.valid is True
    assert len(live_option.available) == 1
    assert live_option.unavailable == []
    assert live_option.available[0].agency_key == "bricodepot:10"
    assert live_option.available[0].supplier_reference == "3334160524579"

    single = next(s for s in response.strategies if s.key == "single_stop")
    assert single.valid is True
    assert any(a.agency_key == "bricodepot:10" for a in single.stops)


def test_flag_off_keeps_historical_db_only_behavior(session):
    _seed_historical_brico_mapping(session)
    settings = Settings(bricodepot_live_enabled=False)
    assert (
        build_live_connectors(
            session, resolved_origin=_origin(), settings=settings
        )
        == []
    )
    connectors = build_connectors(session)
    assert [c.supplier_name for c in connectors] == ["BRICO_DEPOT"]
    # Aucune offre DB → panier incomplet, comportement historique.
    products = {25: ProductRead.model_validate(session.get(Product, 25))}
    response = ComparisonService(
        connectors,
        49.89,
        2.30,
        origin=_origin(),
    ).compare([CartLine(product_id=25, quantity=Decimal("1"))], products)
    assert all(not opt.valid for opt in response.options)
    assert all(not s.valid for s in response.strategies)


def test_db_agency_id_10_does_not_collide_with_live_key(session):
    _seed_historical_brico_mapping(session)
    settings = Settings(bricodepot_live_enabled=True)
    cache = SupplierLiveCacheService(session, settings)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload(10)])
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
    offers = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=EmptyTransport()),
        cache=cache,
        settings=settings,
    ).get_offers([25])
    assert offers[0].agency.agency_key == "bricodepot:10"
    assert offers[0].agency.agency_key != "db:10"
