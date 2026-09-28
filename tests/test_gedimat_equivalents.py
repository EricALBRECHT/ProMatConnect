"""Équivalences Gedimat : mesures, contradictions, candidat unique."""

from app.services.product_mapping.gedimat_equivalents import (
    attributes_conflict,
    expand_synonyms,
    identity_grounded,
    measure_to_mm,
    unsupported_variant,
)


def test_same_length_in_mm_cm_and_m():
    assert measure_to_mm("60 mm") == 60
    assert measure_to_mm("6 cm") == 60
    assert measure_to_mm("0,06 m") == 60
    assert measure_to_mm("0.06 m") == 60


def test_synonym_is_added_in_context_not_replaced():
    text = expand_synonyms("Vis TF TX 5x60")
    assert "Vis TF TX 5x60" in text
    assert "torx" in text
    assert "fraisee" in text
    assert "contreplaque" not in expand_synonyms("CP seul")
    assert "contreplaque" in expand_synonyms("panneau CP 18 mm")


def test_dimension_contradiction_blocks():
    assert attributes_conflict(
        {"diameter_mm": 5, "length_mm": 60},
        {"diameter_mm": 5, "length_mm": 70},
        ("diameter_mm", "length_mm"),
    )
    assert attributes_conflict(
        {"type": "standard"},
        {"type": "hydrofuge"},
        ("type",),
    )
    assert attributes_conflict({"drive": "torx"}, {"drive": "pozidriv"}, ("drive",))


def test_missing_secondary_is_not_a_contradiction():
    assert not attributes_conflict(
        {"diameter_mm": 5, "length_mm": 60, "drive": "torx"},
        {"diameter_mm": 5, "length_mm": 60},
        ("drive", "head", "finish"),
    )


def test_variant_present_on_one_side_only_blocks():
    assert unsupported_variant("Fenêtre isolation totale 100 mm", "Fenêtre pvc 950x600")
    assert unsupported_variant("Raccord femelle 16", "Raccord multicouche 16")
    assert unsupported_variant("Disque diamant carrelage 125", "Disque diamant 125")
    assert not unsupported_variant("Vis trompette 3,5 x 25", "Vis plaque de plâtre 3,5 × 25 mm")


def test_format_must_appear_in_both_names():
    assert identity_grounded(("pvc", "1200x950"), "fenetre 1200x950", "fenetre 1200x950")
    assert not identity_grounded(("beige", "1180x1140"), "store beige SK06", "store 1180x1140 beige")


def test_pack_size_is_not_an_identity_conflict():
    assert not attributes_conflict(
        {"packaging_qty": 100},
        {"packaging_qty": 200},
        ("drive", "head"),
    )
