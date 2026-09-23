from decimal import Decimal

import pytest
from sqlalchemy import select

from app.connectors.fake_gedimat import FakeGedimatConnector
from app.connectors.fake_pointp import FakePointPConnector
from app.models import SupplierProduct
from app.repositories.offers import OfferRepository


@pytest.mark.parametrize(
    "connector_class,name",
    [(FakePointPConnector, "POINT.P TEST"), (FakeGedimatConnector, "GEDIMAT TEST")],
)
def test_connector_common_contract(session, connector_class, name):
    connector = connector_class(OfferRepository(session))
    offers = connector.get_offers([1, 7])
    assert len(offers) == 6
    assert all(o.supplier == name for o in offers)
    assert all(isinstance(o.price, Decimal) for o in offers)
    assert all(o.available_quantity == o.stock * o.reference_quantity for o in offers)
    assert offers[0].model_dump()["available_quantity"] == offers[0].available_quantity
    assert all(o.updated_at.tzinfo is not None for o in offers)
    assert offers == connector.get_offers([1, 7])
    assert connector.get_offers([99999]) == []


def test_inactive_mapping_excluded(session):
    sp = session.scalar(select(SupplierProduct).where(SupplierProduct.product_id == 1))
    sp.active = False
    session.commit()
    assert FakePointPConnector(OfferRepository(session)).get_offers([1]) == []
