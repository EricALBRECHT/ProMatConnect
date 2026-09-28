"""Tests Mapping Batch FASTENER V1 — plusieurs CategoryRule × DIMENSIONAL_FASTENER."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    CATEGORY_CHEVILLE_METAL,
    CATEGORY_VIS_BOIS,
    CATEGORY_VIS_MULTI,
    CORRECTION_SOURCE_EXACT_RULE,
    PROPOSAL_UNMAPPED,
)
from app.services.bricodepot_catalog_import import resolve_brico_supplier
from app.services.product_mapping import pipeline
from app.services.product_mapping.catalog_discover import classify_known_categories
from app.services.product_mapping.cheville_metal_extractor import (
    extract_cheville_metal,
    is_cheville_metal,
)
from app.services.product_mapping.fastener_batch import (
    apply_if_clean,
    audit_exact_matches,
    dry_run_summary,
    seed_fastener_family_products,
)
from app.services.product_mapping.generic_matcher import best_match
from app.services.product_mapping.identity_models import DIMENSIONAL_FASTENER
from app.services.product_mapping.rules import get_rule, registered_codes
from app.services.product_mapping.rules.cheville_metal import CHEVILLE_METAL_RULE
from app.services.product_mapping.rules.vis_bois import VIS_BOIS_RULE
from app.services.product_mapping.rules.vis_multi import VIS_MULTI_RULE
from app.services.product_mapping.vis_bois_extractor import extract_vis_bois, is_vis_bois
from app.services.product_mapping.vis_multi_extractor import (
    extract_vis_multi,
    is_vis_multi,
)


def test_three_rules_share_dimensional_fastener():
    assert VIS_BOIS_RULE.identity is DIMENSIONAL_FASTENER
    assert VIS_MULTI_RULE.identity is DIMENSIONAL_FASTENER
    assert CHEVILLE_METAL_RULE.identity is DIMENSIONAL_FASTENER
    assert VIS_BOIS_RULE.identity_keys == DIMENSIONAL_FASTENER.identity_keys
    codes = registered_codes()
    assert CATEGORY_VIS_BOIS in codes
    assert CATEGORY_VIS_MULTI in codes
    assert CATEGORY_CHEVILLE_METAL in codes


@pytest.mark.parametrize(
    "designation",
    [
        "Vis à bois tête fraisée 4 x 40 mm 200 pièces",
        "Vis bois pozidriv 3,5 x 25 mm",
        "VIS A BOIS ZINGUEE 5 x 60",
    ],
)
def test_classifies_vis_bois(designation):
    assert is_vis_bois(designation) is True
    ext = extract_vis_bois(designation)
    assert ext.classified is True
    assert ext.attributes["type"] == "bois"


@pytest.mark.parametrize(
    "designation",
    [
        "Vis multi-matériaux 4 x 40 mm",
        "Vis tous matériaux 5 x 50 mm 100 pcs",
        "Vis multi-usage 3,5 x 30",
    ],
)
def test_classifies_vis_multi(designation):
    assert is_vis_multi(designation) is True
    ext = extract_vis_multi(designation)
    assert ext.classified is True
    assert ext.attributes["type"] == "multi"


@pytest.mark.parametrize(
    "designation",
    [
        "Cheville métal Molly 8 x 40 mm",
        "Chevilles métalliques à expansion 10 x 50",
        "Cheville acier à frappe 6 x 40 mm 50 pièces",
    ],
)
def test_classifies_cheville_metal(designation):
    assert is_cheville_metal(designation) is True
    ext = extract_cheville_metal(designation)
    assert ext.classified is True
    assert ext.attributes["type"] == "cheville_metal"


@pytest.mark.parametrize(
    "designation",
    [
        "Vis béton 7,5 x 152 mm",
        "Vis multi-matériaux 4 x 40 mm",
        "Vis plaque de plâtre 3,5 x 25",
        "Assortiment vis bois",
        "Kit vis à bois",
        "Cheville nylon 8 x 40",
        "Visseuse bois 18 V",
    ],
)
def test_rejects_non_vis_bois(designation):
    assert is_vis_bois(designation) is False


@pytest.mark.parametrize(
    "designation",
    [
        "Vis à bois 4 x 40 mm",
        "Cheville métal Molly 8 x 40",
        "Assortiment multi-matériaux",
        "Kit vis multi-usage",
        "Ni Clou Ni Vis multi-supports 310 ml",
        "Colle multi-matériaux 290 ml",
    ],
)
def test_rejects_non_vis_multi(designation):
    assert is_vis_multi(designation) is False


@pytest.mark.parametrize(
    "designation",
    [
        "Cheville nylon 8 x 40 mm",
        "Cheville + vis 8 x 40",
        "Assortiment chevilles métal",
        "Vis à bois 4 x 40",
    ],
)
def test_rejects_non_cheville_metal(designation):
    assert is_cheville_metal(designation) is False


def test_bois_and_multi_mutual_exclusion():
    assert is_vis_bois("Vis multi-matériaux 4 x 40 mm") is False
    assert is_vis_multi("Vis à bois 4 x 40 mm") is False


@pytest.mark.parametrize(
    "fn,text,expected",
    [
        (extract_vis_bois, "Vis à bois 3,5 x 25 mm 200 pièces", (3.5, 25)),
        (extract_vis_multi, "Vis multi-matériaux 4 x 40 mm", (4.0, 40)),
        (extract_cheville_metal, "Cheville métal Molly 10 x 50 mm", (10.0, 50)),
        (extract_cheville_metal, "Cheville acier 12 x 60", (12.0, 60)),
    ],
)
def test_extract_diameter_length(fn, text, expected):
    ext = fn(text)
    assert ext.attributes["diameter_mm"] == expected[0]
    assert ext.attributes["length_mm"] == expected[1]


def test_packaging_not_identity_bois():
    a = extract_vis_bois("Vis à bois 4 x 40 mm 100 pièces")
    b = extract_vis_bois("Vis à bois 4 x 40 mm 500 pièces")
    keys = VIS_BOIS_RULE.identity_keys
    assert {k: a.attributes[k] for k in keys} == {k: b.attributes[k] for k in keys}
    assert a.attributes["packaging_qty"] == 100
    assert b.attributes["packaging_qty"] == 500


def test_insufficient_identity():
    ext = extract_vis_bois("Vis à bois tête fraisée sans cote")
    assert ext.classified is True
    assert VIS_BOIS_RULE.identity_complete(ext.attributes) is False
    match = best_match(VIS_BOIS_RULE, ext.attributes, [])
    assert match.status == PROPOSAL_UNMAPPED
    assert match.reason == "INSUFFICIENT_DATA"


def test_no_double_attribution(session):
    """classify_known_categories : un SP → une seule famille."""
    from app.services.product_mapping.catalog_discover import SupplierProductRow

    rows = [
        SupplierProductRow(
            id=1,
            supplier_reference="r1",
            designation="Vis à bois 4 x 40 mm",
            product_id=None,
            correction_source=None,
        ),
        SupplierProductRow(
            id=2,
            supplier_reference="r2",
            designation="Vis multi-matériaux 4 x 40 mm",
            product_id=None,
            correction_source=None,
        ),
        SupplierProductRow(
            id=3,
            supplier_reference="r3",
            designation="Cheville métal Molly 8 x 40 mm",
            product_id=None,
            correction_source=None,
        ),
    ]
    codes = (CATEGORY_CHEVILLE_METAL, CATEGORY_VIS_BOIS, CATEGORY_VIS_MULTI)
    _, assigned = classify_known_categories(session, rows, category_codes=codes)
    assert assigned[1] == CATEGORY_VIS_BOIS
    assert assigned[2] == CATEGORY_VIS_MULTI
    assert assigned[3] == CATEGORY_CHEVILLE_METAL
    assert len(assigned) == 3


def _add_sp(session, supplier_id: int, ref: str, designation: str) -> SupplierProduct:
    sp = SupplierProduct(
        product_id=None,
        supplier_id=supplier_id,
        supplier_reference=ref,
        designation=designation,
        supplier_unit="La pièce",
        reference_quantity=Decimal("1"),
        packaging_quantity=Decimal("1"),
        correction_source="import",
    )
    session.add(sp)
    session.flush()
    return sp


def test_seed_idempotent_and_apply_exact(session):
    supplier = resolve_brico_supplier(session)
    _add_sp(session, supplier.id, "fb-bois-1", "Vis à bois 4 x 40 mm 100 pièces")
    _add_sp(session, supplier.id, "fb-multi-1", "Vis multi-matériaux 5 x 50 mm")
    _add_sp(
        session, supplier.id, "fb-chev-1", "Cheville métal Molly 8 x 40 mm 50 pièces"
    )
    _add_sp(session, supplier.id, "fb-fp", "Assortiment vis bois 4 x 40")  # FP

    for code in (CATEGORY_VIS_BOIS, CATEGORY_VIS_MULTI, CATEGORY_CHEVILLE_METAL):
        first = seed_fastener_family_products(session, code)
        second = seed_fastener_family_products(session, code)
        assert first["created"] >= 1
        assert second["created"] == 0

    # Prefixed codes
    bois = session.scalar(select(Product).where(Product.code == "PMC-VIS-BOIS-4X40"))
    multi = session.scalar(select(Product).where(Product.code == "PMC-VIS-MULTI-5X50"))
    chev = session.scalar(
        select(Product).where(Product.code == "PMC-CHEVILLE-METAL-8X40")
    )
    assert bois is not None and bois.attributes["type"] == "bois"
    assert multi is not None and multi.attributes["type"] == "multi"
    assert chev is not None and chev.attributes["type"] == "cheville_metal"
    assert bois.reference_unit == "pièce"

    for code in (CATEGORY_VIS_BOIS, CATEGORY_VIS_MULTI, CATEGORY_CHEVILLE_METAL):
        audit = audit_exact_matches(session, code)
        assert audit["mismatch_count"] == 0
        applied = apply_if_clean(session, code)
        assert applied["applied"] >= 1
        again = apply_if_clean(session, code)
        assert again["applied"] == 0
        after = dry_run_summary(session, code)
        assert after["exact"] == 0
        assert after["already_mapped"] >= 1


def test_false_positive_kit_not_classified():
    assert is_vis_bois("Kit vis à bois 4 x 40 mm") is False
    assert is_vis_bois("Assortiment vis bois 4 x 40") is False
    assert is_cheville_metal("Assortiment chevilles métal") is False
    assert is_cheville_metal("Cheville + vis 8 x 40") is False
