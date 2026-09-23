from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Agency, Offer, Product, Supplier, SupplierProduct
from app.repositories.catalog import CatalogRepository
from scripts.seed import seed


def test_catalog_creation_read(session):
    product = Product(code="PMC9999", name="Produit test", category="Test", reference_unit="m")
    session.add(product)
    session.commit()
    result = CatalogRepository(session).get(product.id)
    assert result.code == "PMC9999"
    assert result.created_at is not None
    assert result.updated_at is not None


def test_seed_counts(session):
    for model, expected in [
        (Product, 20),
        (Supplier, 2),
        (Agency, 6),
        (SupplierProduct, 40),
        (Offer, 120),
    ]:
        assert session.scalar(select(func.count()).select_from(model)) == expected


def test_seed_idempotent_and_preserves_data(session):
    before = [(o.id, o.price, o.stock, o.updated_at) for o in session.scalars(select(Offer))]
    seed(session)
    after = [(o.id, o.price, o.stock, o.updated_at) for o in session.scalars(select(Offer))]
    assert before == after
    product = session.scalar(select(Product).where(Product.code == "PMC0001"))
    product.description = "Modification à conserver"
    session.commit()
    seed(session)
    assert product.description == "Modification à conserver"


def test_supplier_mapping(session):
    products = list(session.scalars(select(SupplierProduct).where(SupplierProduct.product_id == 1)))
    assert len(products) == 2
    assert len({p.supplier_reference for p in products}) == 2
    assert {p.product_id for p in products} == {1}


def test_search_and_pagination(session):
    repo = CatalogRepository(session)
    assert len(repo.list("BA13")) == 1
    assert len(repo.list("PMC0001")) == 1
    assert repo.list("%") == []
    assert repo.list(limit=2, offset=2)[0].code == "PMC0003"


def test_decimal_prices(session):
    offer = session.scalar(select(Offer))
    assert isinstance(offer.price, Decimal)
    assert offer.price == Decimal("10.49")


def test_offer_cannot_cross_supplier_agency(session):
    offer = session.scalar(select(Offer))
    other_agency = session.scalar(select(Agency).where(Agency.supplier_id != offer.supplier_id))
    offer.agency_id = other_agency.id
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    "field,value", [("price", Decimal("-1")), ("stock", -1), ("preparation_minutes", -5)]
)
def test_offer_constraints(session, field, value):
    offer = session.scalar(select(Offer))
    setattr(offer, field, value)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
