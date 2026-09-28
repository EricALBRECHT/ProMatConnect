"""Tests Batch Discovery V1.1 — nature de cluster + consolidation."""

from __future__ import annotations

from app.services.product_mapping.batch_consolidate import (
    NATURE_ATTRIBUTE,
    NATURE_PRODUCT,
    classify_cluster_nature,
    consolidate_candidates,
    propose_first_lot,
)
from app.services.product_mapping.batch_discover import (
    MODEL_DIMENSIONAL_FASTENER,
    STATUS_READY_EXISTING,
    FamilyQualification,
)
from app.services.product_mapping.catalog_discover import SupplierProductRow


def test_tete_fraisee_is_attribute_cluster():
    nature, reasons = classify_cluster_nature(
        "TETE FRAISEE", parent="VIS", candidate_model=MODEL_DIMENSIONAL_FASTENER
    )
    assert nature == NATURE_ATTRIBUTE
    assert any("attribut" in r for r in reasons)


def test_fraisee_posidriv_is_attribute_cluster():
    nature, _ = classify_cluster_nature(
        "FRAISEE POSIDRIV", parent=None, candidate_model=MODEL_DIMENSIONAL_FASTENER
    )
    assert nature == NATURE_ATTRIBUTE


def test_vis_bois_is_product_family():
    nature, _ = classify_cluster_nature(
        "VIS BOIS", parent=None, candidate_model=MODEL_DIMENSIONAL_FASTENER
    )
    assert nature == NATURE_PRODUCT


def test_cheville_metal_is_product_family():
    nature, _ = classify_cluster_nature(
        "CHEVILLES METAL", parent=None, candidate_model=MODEL_DIMENSIONAL_FASTENER
    )
    assert nature == NATURE_PRODUCT


def _fq(
    family: str,
    ids: list[int],
    *,
    model: str = MODEL_DIMENSIONAL_FASTENER,
    parent: str | None = None,
) -> FamilyQualification:
    return FamilyQualification(
        family=family,
        parent=parent,
        count=len(ids),
        status=STATUS_READY_EXISTING,
        candidate_model=model,
        reasons=[],
        feature_coverage={
            "diameter_mm": {"coverage": 0.95, "distinct": 5, "examples": ["4.0"]},
            "screw_length_mm": {"coverage": 0.95, "distinct": 6, "examples": ["40"]},
            "diameter_x_length": {
                "coverage": 0.9,
                "distinct": 8,
                "examples": ["4.0x40"],
            },
        },
        mapped_count=0,
        unmapped_count=len(ids),
        examples=[],
        complexity="XS",
        member_ids=tuple(ids),
    )


def test_consolidation_merges_attribute_into_product_family():
    # VIS BOIS = 1..20 ; TETE FRAISEE = subset 1..15 (attribut inclus)
    vis = _fq("VIS BOIS", list(range(1, 21)))
    tete = _fq("TETE FRAISEE", list(range(1, 16)), parent="VIS")
    rows = [
        SupplierProductRow(
            id=i,
            supplier_reference=f"s{i}",
            designation=f"Vis bois 4 x {30 + i % 5} mm tête fraisée",
            product_id=None,
            correction_source=None,
        )
        for i in range(1, 21)
    ]
    audited, consolidated, pairs, _fps = consolidate_candidates([vis, tete], rows)
    natures = {a.family: a.nature for a in audited}
    assert natures["VIS BOIS"] == NATURE_PRODUCT
    assert natures["TETE FRAISEE"] == NATURE_ATTRIBUTE
    assert pairs  # chevauchement détecté
    fams = consolidated[MODEL_DIMENSIONAL_FASTENER]
    assert len(fams) == 1
    assert fams[0].name == "VIS BOIS"
    assert "TETE FRAISEE" in fams[0].attribute_clusters
    assert fams[0].unique_sp == 20


def test_first_lot_prefers_existing_model_safe_wins():
    from app.services.product_mapping.batch_consolidate import ConsolidatedFamily

    lot = propose_first_lot(
        {
            MODEL_DIMENSIONAL_FASTENER: [
                ConsolidatedFamily(
                    name="VIS BOIS",
                    candidate_model=MODEL_DIMENSIONAL_FASTENER,
                    unique_sp=50,
                    source_clusters=["VIS BOIS"],
                    attribute_clusters=[],
                    nature=NATURE_PRODUCT,
                    complexity="XS",
                    safe_quick_win=True,
                )
            ],
            "TILE_OR_FLOORING": [
                ConsolidatedFamily(
                    name="CARRELAGE SOL",
                    candidate_model="TILE_OR_FLOORING",
                    unique_sp=200,
                    source_clusters=["CARRELAGE SOL"],
                    attribute_clusters=[],
                    nature=NATURE_PRODUCT,
                    complexity="S",
                    safe_quick_win=True,
                )
            ],
        }
    )
    assert len(lot) == 1
    assert lot[0].name == "VIS BOIS"
