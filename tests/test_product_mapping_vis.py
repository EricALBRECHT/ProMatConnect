"""Tests VIS_PLACO V1 — classification, diamètre × longueur, identité.

Toutes les désignations sont issues du catalogue BRICO_DEPOT réel.
Invariants : le conditionnement n'entre jamais dans l'identité ; une dimension
absente reste UNKNOWN (jamais 0), et une identité incomplète n'est jamais EXACT.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    CATEGORY_VIS_PLACO,
    PROPOSAL_EXACT,
    PROPOSAL_UNMAPPED,
)
from app.services.bricodepot_catalog_import import resolve_brico_supplier
from app.services.product_mapping import pipeline
from app.services.product_mapping.generic_matcher import best_match
from app.services.product_mapping.primitives.diameter import (
    extract_diameter_length_mm,
)
from app.services.product_mapping.primitives.packaging import (
    extract_lot_quantity,
    extract_piece_count,
)
from app.services.product_mapping.rules.vis_placo import VIS_PLACO_RULE
from app.services.product_mapping.vis_extractor import (
    extract_vis_placo,
    is_vis_placo,
)

# --- Classification -------------------------------------------------------

INCLUDED = [
    "Vis plaque de plâtre noires - 4,8 x 100 mm - 200 pièces",
    "Vis Plaque de Plâtre Noires - 4,2 x 70 mm - 2 kg",
    "Seau de 1000 vis plaque de plâtre tête trompette Philips 3,5 x 25 mm acier au carbone",
    "Seau de 1000 vis plaque de plâtre tête trompette Philips autoforeuse Ø 3,5 x 45 mm lubrifiée",
    "Vis plaque de plâtre autoperçante 3,5 x 45 mm - 1 000 pièces",
    "Boîte de 150 vis autoperforante pour plaque de plâtre TTPC 35 mm",
    "Vis pour plaque de plâtre 4,8 x 110 mm - Noir - 200 pièces",
    "Boite de 2 kg de vis plaque de plâtre tête trompette Philips 4,8 x 90 mm acier au carbone",
    "VIS PLAQ PLATRE TTPC 25 BTE1500P",
    "Vis plaque de plâtre TTPC 45 8 x 45 mm - 500 pièces",
    # Graphies fournisseur fautives, présentes au catalogue
    "Vis plaque de pâtre noir - 4,2 X 90 mm - 200 pièces",
    "Vis plaque plâtre noir - 3,5X25 mm - 2 Kg",
]

EXCLUDED = [
    "Visseuse à placo 600 W",
    "Visseuse plaque de plâtre 18 V sans fil  TTI1461DRS",
    "Porte-embout de vissage pour plaque de plâtre - 60 mm",
    "Boite de 2 kg de vis tôle autoforeuse tête fraisée, Philips 4.8 x 50 mm acier carbone",
    "Chevilles autoforeuses nylon 32mm - 10PC  pour plaque de plâtre",
    "Lot de 25 chevilles nylon autoforeuses avec vis L. 30 mm",
    "Kit 25 vis autoperceuses 4,9 x 35 mm avec capuchons pour acier et bois",
    "Kit de 25 tirefonds autoforants 8 x 135 mm acier avec isolants et rondelles",
    "6 VIS BÉTON 7.5X152",
    "Vis terrasse bois inox 5 x 60 mm",
    "Boîte d'assortiment de cheville à expansion et vis DuoPower",
    "Robinet autoperceur avec filetage entrée 20x27 mm",
    "Vis agglo tête fraisée 4 x 40 mm",
    "Boulon TRCC 8 x 60 mm",
    "Vis multi-usage 4 x 30 mm",
    "Rail R48 galvanisé 3 m",
]


@pytest.mark.parametrize("designation", INCLUDED)
def test_classifies_real_vis_placo(designation):
    assert is_vis_placo(designation) is True
    extraction = extract_vis_placo(designation)
    assert extraction.classified is True
    assert extraction.category_code == CATEGORY_VIS_PLACO
    assert extraction.attributes["type"] == "placo"


@pytest.mark.parametrize("designation", EXCLUDED)
def test_rejects_non_vis_placo(designation):
    assert is_vis_placo(designation) is False
    extraction = extract_vis_placo(designation)
    assert extraction.classified is False
    assert extraction.category_code is None
    assert extraction.attributes == {}
    assert extraction.reason == "NOT_THIS_CATEGORY"


def test_visseuse_is_not_a_vis():
    """« visseuse » / « vissage » ne satisfont pas le mot « vis »."""
    assert is_vis_placo("Visseuse placo 18 V") is False
    assert is_vis_placo("Embout de vissage placo") is False


def test_supplier_spelling_variants_of_plaque_de_platre():
    """« plaq platre », « plaque plâtre », « plaque de pâtre » désignent la même famille."""
    for designation in (
        "Vis plaque de plâtre 3,5 x 25 mm",
        "Vis plaques de plâtre 3,5 x 25 mm",
        "Vis plaque plâtre noir - 3,5X25 mm - 2 Kg",
        "Vis plaque de pâtre noir - 4,2 X 90 mm",
        "VIS PLAQ PLATRE TTPC 25",
    ):
        assert is_vis_placo(designation) is True, designation


# --- Diamètre × longueur : formats réels ---------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Vis plaque de plâtre 3,5 x 25 mm", (3.5, 25)),
        ("Vis plaque de plâtre 3,5X25 mm", (3.5, 25)),
        ("Vis plaque de plâtre 3,5 X 45 mm", (3.5, 45)),
        ("Vis plaque de plâtre 3,5  x 35 mm", (3.5, 35)),
        ("Vis autoforeuse Ø 3,5 x 45 mm", (3.5, 45)),
        ("Vis plaque de plâtre 4,2 x 80 mm", (4.2, 80)),
        ("Vis plaque de plâtre 4,8 x 120 mm", (4.8, 120)),
        ("Vis plaque de plâtre 4.8 x 140 mm", (4.8, 140)),
        ("Vis plaque de plâtre noires 3,5 x 55 - 2 kg", (3.5, 55)),
        ("Vis plaque de plâtre 3,5 × 25 mm", (3.5, 25)),
    ],
)
def test_extract_diameter_length_real_formats(text, expected):
    assert extract_diameter_length_mm(text) == expected


def test_diameter_out_of_screw_range_is_unknown():
    # Chevilles / tiges / tirefonds : hors bornes visserie → rien d'inventé
    assert extract_diameter_length_mm("Cheville nylon 12 x 60 mm") == (None, None)
    assert extract_diameter_length_mm("Cheville rallongée Ø10x140") == (None, None)
    assert extract_diameter_length_mm("Vis 3,5 x 250 mm") == (None, None)
    assert extract_diameter_length_mm("Vis 3,5 x 8 mm") == (None, None)
    # La primitive reste générique : c'est le classifieur qui écarte le béton
    assert extract_diameter_length_mm("6 VIS BÉTON 7.5X152") == (7.5, 152)
    assert is_vis_placo("6 VIS BÉTON 7.5X152") is False


def test_packaging_count_is_never_read_as_dimension():
    """« Boîte de 200 » / « 1000 pièces » ne fabriquent pas un couple Ø × L."""
    assert extract_diameter_length_mm("Boîte de 200 vis plaque de plâtre") == (
        None,
        None,
    )
    assert extract_diameter_length_mm("Seau de 1000 vis - 1 000 pièces") == (None, None)
    assert extract_diameter_length_mm("VIS PLAQ PLATRE TTPC 25 BTE1500P") == (None, None)


def test_ttpc_diameter_8_kept_as_extracted():
    """Les lignes TTPC lisent 8 × 45 : on garde la valeur lue, sans la corriger."""
    assert extract_diameter_length_mm("Vis plaque de plâtre TTPC 45 8 x 45 mm") == (
        8.0,
        45,
    )
    assert extract_diameter_length_mm("Boîte de 500 vis plaque de plâtre TTPC 8 x 35 mm") == (
        8.0,
        35,
    )


# --- Conditionnement : attribut, jamais identité -------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Vis plaque de plâtre - 200 pièces", 200),
        ("Vis plaque de plâtre - 1 000 pièces", 1000),
        ("Seau de 1000 vis plaque de plâtre", 1000),
        ("Boite de 200 vis plaque de plâtre", 200),
        ("Boîte 1500 vis pour plaque de plâtre TTPC 45 tête plate", 1500),
        ("Lot de 50 vis", 50),
        ("Paquet de 200 vis plaque de plâtre", 200),
    ],
)
def test_extract_piece_count(text, expected):
    assert extract_piece_count(text) == expected


def test_boite_de_2_kg_is_not_a_piece_count():
    """Une masse n'est pas un dénombrement — 2 kg ne vaut pas 2 pièces."""
    assert extract_piece_count("Boite de 2 kg de vis plaque de plâtre") is None
    assert extract_lot_quantity("Boite de 2 kg de vis plaque de plâtre") is None


def test_lot_quantity_behaviour_preserved():
    assert extract_lot_quantity("Lot de 10 montants M4835") == 10
    assert extract_lot_quantity("Montant M48 seul") is None


def test_packaging_is_not_part_of_identity():
    assert "packaging_qty" not in VIS_PLACO_RULE.identity_keys
    boite = extract_vis_placo(
        "Boite de 2 kg de vis plaque de plâtre tête trompette Philips 3,5 x 25 mm lubrifiée"
    )
    seau = extract_vis_placo(
        "Seau de 1000 vis plaque de plâtre tête trompette Philips 3,5 x 25 mm acier au carbone"
    )
    identity = VIS_PLACO_RULE.identity_keys
    assert {k: boite.attributes[k] for k in identity} == {
        k: seau.attributes[k] for k in identity
    }
    # ... alors que le conditionnement, lui, diffère
    assert boite.attributes["packaging_qty"] != seau.attributes["packaging_qty"]


# --- Identité incomplète -------------------------------------------------

def test_missing_dimensions_stay_unknown():
    extraction = extract_vis_placo(
        "Boîte de 150 vis autoperforante pour plaque de plâtre TTPC 35 mm"
    )
    assert extraction.classified is True
    assert extraction.attributes["diameter_mm"] is None
    assert extraction.attributes["length_mm"] is None  # jamais 0
    assert VIS_PLACO_RULE.identity_complete(extraction.attributes) is False


# --- Matching contre les PMC existants -----------------------------------

_PMC_35X25 = (
    31,
    "PMC-VIS-PLACO-35X25",
    {"type": "placo", "diameter_mm": 3.5, "length_mm": 25},
    "Vis placo",
)
_PMC_35X35 = (
    32,
    "PMC-VIS-PLACO-35X35",
    {"type": "placo", "diameter_mm": 3.5, "length_mm": 35},
    "Vis placo",
)
_PMC_VIS = [_PMC_35X25, _PMC_35X35]


def test_exact_on_existing_pmc():
    extraction = extract_vis_placo(
        "Seau de 1000 vis plaque de plâtre tête trompette Philips 3,5 x 25 mm acier au carbone"
    )
    match = best_match(VIS_PLACO_RULE, extraction.attributes, _PMC_VIS)
    assert match.status == PROPOSAL_EXACT
    assert match.product_code == "PMC-VIS-PLACO-35X25"


def test_length_absent_from_pmc_is_no_pmc_product():
    """3,5 × 45 : même famille, longueur absente du catalogue PMC."""
    extraction = extract_vis_placo(
        "Vis plaque de plâtre autoperçante 3,5 x 45 mm - 1 000 pièces"
    )
    match = best_match(VIS_PLACO_RULE, extraction.attributes, _PMC_VIS)
    assert match.status == PROPOSAL_UNMAPPED
    assert match.reason == "NO_PMC_PRODUCT"
    assert match.product_id is None


def test_other_diameter_never_matches_35():
    """4,2 × 70 ne doit jamais être rattaché à une vis 3,5."""
    extraction = extract_vis_placo("Vis plaque de plâtre noires - 4,2 x 70 mm - 200 pièces")
    match = best_match(VIS_PLACO_RULE, extraction.attributes, _PMC_VIS)
    assert match.status == PROPOSAL_UNMAPPED
    assert match.reason == "NO_PMC_PRODUCT"


def test_ttpc_8x35_is_not_matched_to_35x35():
    extraction = extract_vis_placo("Boîte de 500 vis plaque de plâtre TTPC 8 x 35 mm")
    match = best_match(VIS_PLACO_RULE, extraction.attributes, _PMC_VIS)
    assert match.status == PROPOSAL_UNMAPPED
    assert match.reason == "NO_PMC_PRODUCT"


def test_incomplete_identity_is_insufficient_not_exact():
    extraction = extract_vis_placo(
        "Boîte de 150 vis autoperforante pour plaque de plâtre TTPC 35 mm"
    )
    match = best_match(VIS_PLACO_RULE, extraction.attributes, _PMC_VIS)
    assert match.status == PROPOSAL_UNMAPPED
    assert match.reason == "INSUFFICIENT_DATA"
    assert match.product_id is None


def test_no_pmc_candidate_at_all_is_no_pmc_product():
    extraction = extract_vis_placo("Vis plaque de plâtre 3,5 x 25 mm - 200 pièces")
    match = best_match(VIS_PLACO_RULE, extraction.attributes, [])
    assert match.status == PROPOSAL_UNMAPPED
    assert match.reason == "NO_PMC_PRODUCT"


# --- Pipeline générique, sans code dédié ---------------------------------

def test_pipeline_runs_vis_placo_rule(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    supplier = resolve_brico_supplier(session)
    designations = [
        "Seau de 1000 vis plaque de plâtre tête trompette Philips 3,5 x 25 mm acier au carbone",
        "Vis plaque de plâtre autoperçante 3,5 x 45 mm - 1 000 pièces",
        "Boîte de 150 vis autoperforante pour plaque de plâtre TTPC 35 mm",
        # Passe le pré-filtre SQL (« autoforeuse ») mais écarté : support tôle
        "Boite de 2 kg de vis tôle autoforeuse tête fraisée, Philips 4.8 x 50 mm acier carbone",
        # 8,0×… : pas de PMC-VIS-PLACO-80X* dans le catalogue normalisé
        "Vis plaque de plâtre 8,0 x 25 mm - 100 pièces",
    ]
    for index, designation in enumerate(designations):
        session.add(
            SupplierProduct(
                product_id=None,
                supplier_id=supplier.id,
                supplier_reference=f"vis{index:03d}",
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
        VIS_PLACO_RULE,
        supplier_name=supplier.name,
        limit=None,
        all_candidates=True,
    )

    assert run.category_code == CATEGORY_VIS_PLACO
    assert run.exact >= 1
    assert run.no_pmc_product >= 1
    assert run.insufficient_data >= 1
    # Le pré-filtre SQL laisse passer la visseuse, le classifieur l'écarte
    assert run.not_this_category >= 1

    exact_item = next(i for i in run.items if i.reason == "EXACT")
    assert exact_item.match is not None
    assert exact_item.match.product_code == "PMC-VIS-PLACO-35X25"
    assert exact_item.attrs["packaging_qty"] == 1000

    # Analyse dry-run : aucun product_id posé
    for item in run.items:
        assert item.sp.product_id is None


def test_pipeline_creates_vis_category_schema(session):
    category = pipeline.ensure_category(session, VIS_PLACO_RULE)
    assert category.code == CATEGORY_VIS_PLACO
    assert category.name == "Vis placo"


def test_pmc_candidates_are_scoped_to_vis_placo(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.flush()
    candidates = pipeline.load_pmc_candidates(session, VIS_PLACO_RULE)
    codes = {code for _pid, code, _attrs, _sub in candidates}
    assert "PMC-VIS-PLACO-35X25" in codes
    assert "PMC-VIS-PLACO-35X35" in codes
    assert not any(code.startswith("PMC-BA") for code in codes)
    plaques = session.scalars(select(Product).where(Product.code.like("PMC-BA%"))).all()
    assert plaques  # les plaques existent bien, mais hors périmètre VIS
