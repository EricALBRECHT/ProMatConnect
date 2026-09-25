"""Référentiel Product normalisé + legacy + mapping."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Product
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import ProductCreate
from scripts.normalized_catalog import (
    NORMALIZED_PRODUCT_COUNT,
    mark_demo_products_legacy,
    seed_normalized_catalog,
)
from scripts.seed import seed


def test_normalized_catalog_idempotent(session):
    before = session.scalar(select(func.count()).select_from(Product))
    assert seed_normalized_catalog(session) == 0  # déjà seedé par conftest
    session.commit()
    after = session.scalar(select(func.count()).select_from(Product))
    assert before == after == 20 + NORMALIZED_PRODUCT_COUNT


def test_demo_products_preserved_as_legacy(session):
    demo = session.scalar(select(Product).where(Product.code == "PMC0001"))
    assert demo is not None
    assert demo.name.startswith("Plaque BA13")
    assert demo.is_legacy is True
    assert demo.is_active is True
    # Nom historique non recyclé
    std = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    assert std is not None
    assert std.id != demo.id
    assert std.is_legacy is False


def test_ba13_variants_are_distinct_products(session):
    codes = [
        "PMC-BA13-STD-2500X1200",
        "PMC-BA13-HYDRO-2500X1200",
        "PMC-BA13-MULTI-2500X1200",
        "PMC-BA13-LIGHT-2500X1200",
    ]
    products = list(session.scalars(select(Product).where(Product.code.in_(codes))))
    assert len(products) == 4
    assert len({p.id for p in products}) == 4


def test_insulation_lambda_variants_distinct(session):
    a = session.scalar(select(Product).where(Product.code == "PMC-LDV-MUR-100-L32-R315-KRAFT"))
    b = session.scalar(select(Product).where(Product.code == "PMC-LDV-MUR-100-L40-R250"))
    assert a is not None and b is not None
    assert a.id != b.id
    assert a.attributes["thermal_lambda"] == 0.032
    assert b.attributes["thermal_lambda"] == 0.040


def test_mapping_search_excludes_legacy(session):
    repo = CatalogRepository(session)
    legacy_hits = repo.list("PMC0001", for_mapping=True)
    assert legacy_hits == []
    hits = repo.list("ba13", for_mapping=True)
    assert hits
    assert all(not p.is_legacy for p in hits)
    assert any(p.code == "PMC-BA13-STD-2500X1200" for p in hits)


def test_create_product_api(client, session):
    response = client.post(
        "/api/products",
        json={
            "code": "PMC-TEST-CREATE-001",
            "name": "Produit créé pour test mapping",
            "category": "Test",
            "subcategory": "Unitaire",
            "reference_unit": "pièce",
            "attributes": {"note": "test"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "PMC-TEST-CREATE-001"
    assert body["is_legacy"] is False
    found = client.get(
        "/api/products", params={"q": "PMC-TEST-CREATE", "for_mapping": True}
    ).json()
    assert any(p["code"] == "PMC-TEST-CREATE-001" for p in found)
    # Doublon refusé
    again = client.post(
        "/api/products",
        json={
            "code": "PMC-TEST-CREATE-001",
            "name": "Dup",
            "category": "Test",
            "reference_unit": "pièce",
        },
    )
    assert again.status_code == 409


def test_seed_marks_legacy_without_destroying(session):
    demo = session.scalar(select(Product).where(Product.code == "PMC0001"))
    original_name = demo.name
    mark_demo_products_legacy(session)
    session.commit()
    assert demo.name == original_name
    seed(session)
    assert session.scalar(select(Product).where(Product.code == "PMC0001")).name == original_name
