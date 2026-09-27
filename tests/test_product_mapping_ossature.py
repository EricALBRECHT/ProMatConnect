"""Tests OSSATURE_PLACO V1.1 — longueur nominale + matching strict table."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Product
from app.models.product_mapping import (
    KIND_FOURRURE,
    KIND_MONTANT,
    PROPOSAL_EXACT,
    PROPOSAL_UNMAPPED,
)
from app.services.product_mapping.ossature_extractor import (
    REASON_NOT_THIS_CATEGORY,
    extract_ossature_placo,
    nominal_length_mm,
)
from app.services.product_mapping.ossature_matcher import best_match, score_against_product


def test_nominal_length_table():
    assert nominal_length_mm(2490) == 2500
    assert nominal_length_mm(2500) == 2500
    assert nominal_length_mm(2990) == 3000
    assert nominal_length_mm(3000) == 3000
    # hors table — pas d'arrondi
    assert nominal_length_mm(2480) == 2480
    assert nominal_length_mm(2510) == 2510
    assert nominal_length_mm(2980) == 2980
    assert nominal_length_mm(3010) == 3010
    assert nominal_length_mm(None) is None


def test_extract_preserves_real_length_and_sets_nominal():
    r = extract_ossature_placo(
        designation="Lot de 10 montants M4835 NF - 48 x 2490 mm"
    )
    assert r.attributes["length_mm"] == 2490
    assert r.attributes["nominal_length_mm"] == 2500
    assert r.attributes["profile"] == "M48"


def test_m48_2490_exact_to_2500_pmc():
    extracted = {
        "kind": KIND_MONTANT,
        "profile": "M48",
        "length_mm": 2490,
        "nominal_length_mm": 2500,
    }
    m = score_against_product(
        extracted,
        product_id=1,
        product_code="PMC-MONTANT-M48-2500",
        product_attrs={"profile": "M48", "length_mm": 2500},
        subcategory="Montants",
    )
    assert m.status == PROPOSAL_EXACT
    assert m.reason == "EXACT"


def test_m48_2990_exact_to_3000_pmc():
    extracted = {
        "kind": KIND_MONTANT,
        "profile": "M48",
        "length_mm": 2990,
        "nominal_length_mm": 3000,
    }
    m = best_match(
        extracted,
        [
            (
                1,
                "PMC-MONTANT-M48-3000",
                {"profile": "M48", "length_mm": 3000},
                "Montants",
            )
        ],
    )
    assert m.status == PROPOSAL_EXACT


def test_no_tolerance_2480():
    extracted = {
        "kind": KIND_MONTANT,
        "profile": "M48",
        "length_mm": 2480,
        "nominal_length_mm": 2480,
    }
    m = best_match(
        extracted,
        [
            (
                1,
                "PMC-MONTANT-M48-2500",
                {"profile": "M48", "length_mm": 2500},
                "Montants",
            )
        ],
    )
    assert m.status == PROPOSAL_UNMAPPED
    assert m.reason == "NO_PMC_PRODUCT"


def test_rail_r48_3000_still_exact():
    r = extract_ossature_placo(designation="Rail R48 - 3 m NF")
    assert r.attributes["length_mm"] == 3000
    assert r.attributes["nominal_length_mm"] == 3000
    m = score_against_product(
        r.attributes,
        product_id=1,
        product_code="PMC-RAIL-R48-3000",
        product_attrs={"profile": "R48", "length_mm": 3000},
        subcategory="Rails",
    )
    assert m.status == PROPOSAL_EXACT


def test_exclude_montante_and_kit():
    assert extract_ossature_placo(
        designation='Chaussures de sécurité montantes "Chukka" S1P'
    ).reason == REASON_NOT_THIS_CATEGORY
    assert extract_ossature_placo(
        designation="Kit porte coulissante + rail alu"
    ).classified is False


def test_m70_and_f45_5300_new_pmc(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.flush()
    m70 = session.scalar(select(Product).where(Product.code == "PMC-MONTANT-M70-2500"))
    f45 = session.scalar(select(Product).where(Product.code == "PMC-FOURRURE-F45-5300"))
    assert m70 is not None
    assert m70.attributes == {"profile": "M70", "length_mm": 2500}
    assert f45 is not None
    assert f45.attributes == {"profile": "F45", "length_mm": 5300}

    r_m = extract_ossature_placo(designation="Montant M70 - 2,50 m NF")
    assert best_match(
        r_m.attributes,
        [(m70.id, m70.code, m70.attributes, m70.subcategory)],
    ).status == PROPOSAL_EXACT

    r_f = extract_ossature_placo(
        designation="Fourrure profilée galvanisée F45 - 5,3 m NF"
    )
    assert r_f.attributes["length_mm"] == 5300
    assert best_match(
        r_f.attributes,
        [(f45.id, f45.code, f45.attributes, f45.subcategory)],
    ).status == PROPOSAL_EXACT

    # Idempotence seed
    n = seed_normalized_catalog(session)
    session.flush()
    assert n == 0
    assert session.scalar(select(Product).where(Product.code == "PMC-MONTANT-M70-2500"))


def test_fourrure_f45_3000():
    r = extract_ossature_placo(designation="Fourrure profilée galvanisée F45 - 3 m NF")
    assert r.attributes["kind"] == KIND_FOURRURE
    assert r.attributes["nominal_length_mm"] == 3000
