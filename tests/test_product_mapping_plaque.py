"""Tests V1 mapping PLAQUE_PLATRE — extracteur + matcher + non-mutation product_id."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    PROPOSAL_EXACT,
    PROPOSAL_REVIEW,
    PROPOSAL_UNMAPPED,
    ProductCategory,
)
from app.services.bricodepot_catalog_import import resolve_brico_supplier
from app.services.product_mapping.plaque_extractor import (
    extract_dimensions_mm,
    extract_plaque_platre,
    extract_plaque_type_and_flags,
)
from app.services.product_mapping.plaque_matcher import best_match, score_against_product
from app.services.product_mapping.service import (
    ProductMappingService,
    ensure_plaque_platre_category,
)


def test_extract_dimensions_mm_variants():
    assert extract_dimensions_mm("Plaque 2500 x 1200 x 13") == (2500, 1200, 13)
    assert extract_dimensions_mm("2500×1200×13mm") == (2500, 1200, 13)
    assert extract_dimensions_mm("L.2,50 x l.1,20m x Ep.13mm") == (2500, 1200, 13)
    assert extract_dimensions_mm("2,50 x 1,20 m x 13 mm") == (2500, 1200, 13)
    bad = extract_dimensions_mm("pas de dimensions ici")
    assert bad == (None, None, None)


def test_extract_types_and_unknown_flags():
    std = extract_plaque_type_and_flags("Plaque BA13 standard 2500x1200x13")
    assert std["type"] == "standard"
    assert std["hydrofuge"] is None  # UNKNOWN ≠ false
    assert std["fire_resistant"] is None
    assert std["acoustic"] is None

    hydro = extract_plaque_type_and_flags("Plaque de plâtre BA13 hydrofuge 2500x1200x13")
    assert hydro["type"] == "hydrofuge"
    assert hydro["hydrofuge"] is True

    feu = extract_plaque_type_and_flags("Plaque BA13 coupe-feu 2500 x 1200 x 13")
    assert feu["type"] == "feu"
    assert feu["fire_resistant"] is True

    light = extract_plaque_type_and_flags("Plaque BA13 Purelight 2500x1200x13")
    assert light["type"] == "legere"


def test_unknown_not_false_in_extraction_result():
    r = extract_plaque_platre(designation="Plaque BA13 2500 x 1200 x 13 mm")
    assert r.classified is True
    assert r.attributes["type"] == "standard"
    assert r.attributes["hydrofuge"] is None
    assert r.attributes["fire_resistant"] is None
    assert "false" not in str(r.attributes["hydrofuge"]).lower() or r.attributes["hydrofuge"] is None


def test_exact_match_same_dims_type():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "standard",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    product_attrs = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "standard",
        "surface_m2": 3.0,
    }
    m = score_against_product(
        extracted, product_attrs, product_id=1, product_code="PMC-BA13-STD-2500X1200"
    )
    assert m.status == PROPOSAL_EXACT
    assert m.score == 1.0


def test_different_dims_not_exact():
    extracted = {
        "length_mm": 2500,
        "width_mm": 600,
        "thickness_mm": 13,
        "type": "standard",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    product_attrs = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "standard",
    }
    m = score_against_product(
        extracted, product_attrs, product_id=1, product_code="PMC-X"
    )
    assert m.status != PROPOSAL_EXACT


def test_hydro_incompatible_not_exact():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "hydrofuge",
        "hydrofuge": True,
        "fire_resistant": None,
        "acoustic": None,
    }
    product_attrs = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "standard",
    }
    m = score_against_product(
        extracted, product_attrs, product_id=1, product_code="PMC-STD"
    )
    assert m.status != PROPOSAL_EXACT


def test_missing_info_no_fake_exact():
    # dimensions OK, type manquant → pas EXACT
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": None,
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    product_attrs = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "standard",
    }
    m = score_against_product(
        extracted, product_attrs, product_id=1, product_code="PMC-STD"
    )
    assert m.status == PROPOSAL_REVIEW
    assert m.status != PROPOSAL_EXACT


def test_best_match_picks_exact_among_candidates():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "legere",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    candidates = [
        (1, "PMC-BA13-STD-2500X1200", {"length_mm": 2500, "width_mm": 1200, "thickness_mm": 13, "type": "standard"}),
        (2, "PMC-BA13-LIGHT-2500X1200", {"length_mm": 2500, "width_mm": 1200, "thickness_mm": 13, "type": "legere"}),
    ]
    m = best_match(extracted, candidates)
    assert m.status == PROPOSAL_EXACT
    assert m.product_code == "PMC-BA13-LIGHT-2500X1200"


def test_proposal_never_changes_product_id(session):
    ensure_plaque_platre_category(session)
    # Product BA13 existant
    product = session.scalar(
        select(Product).where(Product.code == "PMC-BA13-LIGHT-2500X1200")
    )
    if product is None:
        product = Product(
            code="PMC-BA13-LIGHT-2500X1200",
            name="Plaque BA13 Purelight",
            category="Plâtrerie",
            subcategory="Plaques de plâtre",
            reference_unit="pièce",
            attributes={
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "legere",
            },
        )
        session.add(product)
        session.flush()

    supplier = resolve_brico_supplier(session)
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=supplier.id,
        supplier_reference="9990000000001",
        designation="Plaque de plâtre BA13 Purelight 2500 x 1200 x 13 mm",
        supplier_unit="La pièce",
        reference_quantity=Decimal("1"),
        packaging_quantity=Decimal("1"),
        correction_source="manual",
    )
    session.add(sp)
    session.flush()
    old_pid = sp.product_id

    svc = ProductMappingService(session)
    result = svc.run_plaque_platre(
        supplier_name=supplier.name,
        limit=10,
        dry_run=False,
        persist=True,
        example_limit=3,
    )
    session.flush()
    session.refresh(sp)
    assert sp.product_id == old_pid
    assert sp.correction_source == "manual"
    assert result.classified >= 1
    cat = session.scalar(
        select(ProductCategory).where(ProductCategory.code == "PLAQUE_PLATRE")
    )
    assert cat is not None


def test_ensure_category_idempotent(session):
    a = ensure_plaque_platre_category(session)
    b = ensure_plaque_platre_category(session)
    assert a.id == b.id
