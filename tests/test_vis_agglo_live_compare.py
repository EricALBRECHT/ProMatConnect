"""VIS agglo : multi-SP → live → conditionnement → ComparisonService."""

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
    BricoDepotConnector,
)
from app.database import Base
from app.models import Product, Supplier, SupplierProduct
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.schemas.location import ResolvedOrigin
from app.services.comparison import ComparisonService, required_packs
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


def _origin() -> ResolvedOrigin:
    return ResolvedOrigin(
        type="site",
        label="Amiens",
        source="geopf",
        latitude=49.89,
        longitude=2.30,
        citycode="80021",
        city="Amiens",
        postcode="80000",
    )


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


def _seed_vis_agglo(session: Session) -> Product:
    product = Product(
        id=74,
        code="PMC-VIS-AGGLO-35X25",
        name="Vis agglo 3,5 × 25 mm",
        category="Fixation",
        reference_unit="pièce",
        attributes={"type": "agglo", "diameter_mm": 3.5, "length_mm": 25},
    )
    supplier = Supplier(name="BRICO_DEPOT", source_type="file", source_key="test")
    session.add_all([product, supplier])
    session.flush()
    for sp_id, sku, designation in (
        (7628, "3663602746638", "Vis agglo turbo 3,5 x 25 mm 500 pièces"),
        (7640, "3663602747567", "Vis agglo plates 3,5 x 25 mm 100 pièces"),
    ):
        session.add(
            SupplierProduct(
                id=sp_id,
                product_id=74,
                supplier_id=supplier.id,
                supplier_reference=sku,
                designation=designation,
                supplier_unit="La pièce",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                correction_source="exact_rule",
                active=True,
            )
        )
    session.commit()
    return product


def test_required_packs_uses_piece_count_from_designation(session):
    _seed_vis_agglo(session)
    sp = session.get(SupplierProduct, 7628)
    from app.services.conditioning import resolve_reference_quantity

    ref = resolve_reference_quantity(sp)
    assert ref == Decimal("500")
    assert required_packs(Decimal("700"), ref) == 2


def test_live_multi_sp_picks_cheapest_with_stock(session):
    product = _seed_vis_agglo(session)
    settings = Settings(bricodepot_live_enabled=True, bricodepot_max_stores=1)
    cache = SupplierLiveCacheService(session, settings)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    # 500-pack : 2 boîtes pour 700 vis ; 100-pack : 7 boîtes — le 500-pack gagne.
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3663602746638",
        price_ht=Decimal("4.17"),
        price_ttc=Decimal("5.00"),
        stock_quantity=10,
        is_salable=True,
        is_offer_available=True,
    )
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3663602747567",
        price_ht=Decimal("1.67"),
        price_ttc=Decimal("2.00"),
        stock_quantity=20,
        is_salable=True,
        is_offer_available=True,
    )
    session.commit()

    live = BricoDepotConnector(
        session,
        insee_code="80021",
        client=BricoDepotClient(transport=lambda **kwargs: (_ for _ in ()).throw(AssertionError("no http"))),
        cache=cache,
        settings=settings,
    )
    offers = live.get_offers([74])
    assert len(offers) == 2
    by_ref = {o.supplier_reference: o for o in offers}
    assert by_ref["3663602746638"].reference_quantity == Decimal("500")
    assert by_ref["3663602747567"].reference_quantity == Decimal("100")

    products = {74: ProductRead.model_validate(product)}
    response = ComparisonService(
        [live],
        49.89,
        2.30,
        origin=_origin(),
    ).compare([CartLine(product_id=74, quantity=Decimal("700"))], products)

    option = response.options[0]
    assert option.valid is True
    line = option.available[0]
    assert line.supplier_reference == "3663602746638"
    assert line.packs == 2
    assert line.line_total == Decimal("8.34")


def test_out_of_stock_live_yields_unavailable(session):
    product = _seed_vis_agglo(session)
    settings = Settings(bricodepot_live_enabled=True)
    cache = SupplierLiveCacheService(session, settings)
    cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload()])
    cache.put_offer(
        CACHE_CONNECTOR_KEY,
        "10",
        "3663602746638",
        price_ht=Decimal("4.17"),
        price_ttc=Decimal("5.00"),
        stock_quantity=0,
        is_salable=False,
        is_offer_available=True,
    )
    session.commit()
    live = BricoDepotConnector(
        session,
        insee_code="80021",
        cache=cache,
        settings=settings,
    )
    products = {74: ProductRead.model_validate(product)}
    response = ComparisonService(
        [live],
        49.89,
        2.30,
        origin=_origin(),
    ).compare([CartLine(product_id=74, quantity=Decimal("1"))], products)
    assert response.options[0].valid is False
    assert response.options[0].unavailable
