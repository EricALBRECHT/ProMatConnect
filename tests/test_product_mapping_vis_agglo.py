"""Tests VIS_AGGLO V1 — classification, diamètre × longueur, identité."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    CATEGORY_VIS_AGGLO,
    PROPOSAL_UNMAPPED,
)
from app.services.bricodepot_catalog_import import resolve_brico_supplier
from app.services.product_mapping import pipeline
from app.services.product_mapping.generic_matcher import best_match
from app.services.product_mapping.primitives.diameter import extract_diameter_length_mm
from app.services.product_mapping.primitives.packaging import extract_piece_count
from app.services.product_mapping.rules.vis_agglo import VIS_AGGLO_RULE
from app.services.product_mapping.vis_agglo_extractor import (
    extract_vis_agglo,
    is_vis_agglo,
)

INCLUDED = [
    "Vis agglo tête fraisée 4 x 40 mm",
    "Vis agglo turbo 3,5 x 20 mm 100 pièces",
    "Vis agglo plates fraisées 4,5 x 40 mm 500 pièces",
    "Vis agglo fraisées inox A2 5 x 70 mm 20",
    "Vis agglo Q195 or 2,65 x 50 mm 1 kg",
    "Vis agglo plates TX 6 x 100 mm 100 pcs",
    "Vis agglo turbo 4 x 50 mm 500 pièces",
    "Boîte de 500 vis agglo plates 5 x 40 mm",
]

EXCLUDED = [
    "Vis plaque de plâtre noires - 4,8 x 100 mm - 200 pièces",
    "Vis terrasse bois inox 5 x 60 mm",
    "Chevilles autoforeuses nylon 32mm - 10PC  pour plaque de plâtre",
    "6 VIS BÉTON 7.5X152",
    "Vis multi-usage 4 x 30 mm",
    "Visseuse à placo 600 W",
    "Boite de 2 kg de vis tôle autoforeuse tête fraisée, Philips 4.8 x 50 mm",
    "Boulon TRCC 8 x 60 mm",
    "Boîte d'assortiment de cheville à expansion et vis DuoPower",
]


@pytest.mark.parametrize("designation", INCLUDED)
def test_classifies_vis_agglo(designation):
    assert is_vis_agglo(designation) is True
    extraction = extract_vis_agglo(designation)
    assert extraction.classified is True
    assert extraction.category_code == CATEGORY_VIS_AGGLO
    assert extraction.attributes["type"] == "agglo"


@pytest.mark.parametrize("designation", EXCLUDED)
def test_rejects_non_vis_agglo(designation):
    assert is_vis_agglo(designation) is False
    extraction = extract_vis_agglo(designation)
    assert extraction.classified is False
    assert extraction.category_code is None
    assert extraction.attributes == {}
    assert extraction.reason == "NOT_THIS_CATEGORY"


def test_placo_and_agglo_mutual_exclusion():
    assert is_vis_agglo("Vis plaque de plâtre 3,5 x 25 mm") is False
    from app.services.product_mapping.vis_extractor import is_vis_placo

    assert is_vis_placo("Vis agglo turbo 4 x 50 mm") is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Vis agglo turbo 3,5 x 20 mm 100 pièces", (3.5, 20)),
        ("Vis agglo plates fraisées 4,5 x 40 mm 500 pièces", (4.5, 40)),
        ("Vis agglo Q195 or 2,65 x 50 mm 1 kg", (2.65, 50)),
        ("Vis agglo fraisées inox A2 5 x 70 mm 20", (5.0, 70)),
        ("Vis agglo plates TX 6 x 100 mm 100 pcs", (6.0, 100)),
        ("Vis agglo plate 8 x 220 mm 1 pcs", (8.0, 220)),
        ("VIS AGGLO GRANDE LONGUEUR 8 x 260", (8.0, 260)),
    ],
)
def test_extract_diameter_length_agglo(text, expected):
    ext = extract_vis_agglo(text)
    assert ext.attributes["diameter_mm"] == expected[0]
    assert ext.attributes["length_mm"] == expected[1]


def test_packaging_not_identity():
    assert "packaging_qty" not in VIS_AGGLO_RULE.identity_keys
    a = extract_vis_agglo("Vis agglo turbo 3,5 x 20 mm 100 pièces")
    b = extract_vis_agglo("Vis agglo turbo 3,5 x 20 mm 500 pièces")
    keys = VIS_AGGLO_RULE.identity_keys
    assert {k: a.attributes[k] for k in keys} == {k: b.attributes[k] for k in keys}
    assert a.attributes["packaging_qty"] == 100
    assert b.attributes["packaging_qty"] == 500


def test_secondary_attrs_optional():
    ext = extract_vis_agglo("Vis agglo plates fraisées inox 4,5 x 40 mm 500 pièces")
    assert ext.attributes.get("head") == "fraisee"
    assert ext.attributes.get("finish") == "inox"


def test_missing_identity_incomplete():
    ext = extract_vis_agglo("Vis agglo plates sans cote")
    assert ext.classified is True
    assert ext.attributes["diameter_mm"] is None
    assert VIS_AGGLO_RULE.identity_complete(ext.attributes) is False


def test_no_pmc_when_catalog_empty():
    ext = extract_vis_agglo("Vis agglo turbo 3,5 x 20 mm 100 pièces")
    match = best_match(VIS_AGGLO_RULE, ext.attributes, [])
    assert match.status == PROPOSAL_UNMAPPED
    assert match.reason == "NO_PMC_PRODUCT"


def test_pipeline_run_vis_agglo(session):
    supplier = resolve_brico_supplier(session)
    designations = [
        "Vis agglo turbo 7 x 80 mm 50 pièces",
        "Vis agglo plates sans cote",
        "Panneau aggloméré L. 2,50 m x l. 1,25 m x Ép. 18 mm",
    ]
    for index, designation in enumerate(designations):
        session.add(
            SupplierProduct(
                product_id=None,
                supplier_id=supplier.id,
                supplier_reference=f"agglo{index:03d}",
                designation=designation,
                supplier_unit="La pièce",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                correction_source="import",
            )
        )
    session.flush()

    run = pipeline.run_category(
        session,
        VIS_AGGLO_RULE,
        supplier_name=supplier.name,
        limit=None,
        all_candidates=True,
    )
    assert run.category_code == CATEGORY_VIS_AGGLO
    assert run.classified >= 2
    assert run.not_this_category >= 1
    assert run.no_pmc_product >= 1
    assert run.insufficient_data >= 1


def test_pmc_candidates_scoped(session):
    candidates = pipeline.load_pmc_candidates(session, VIS_AGGLO_RULE)
    codes = {code for _pid, code, _attrs, _sub in candidates}
    assert "PMC-VIS-AGGLO-35X20" in codes
    plaques = session.scalars(select(Product).where(Product.code.like("PMC-BA%"))).all()
    assert not any(p.code in codes for p in plaques)
