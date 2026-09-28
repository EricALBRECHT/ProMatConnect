"""Tests Batch Discovery V1 — qualification déterministe (READ-ONLY)."""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import func, select

from app.models import Supplier, SupplierProduct
from app.services.product_mapping.batch_discover import (
    MODEL_BOARD_PANEL,
    MODEL_DIMENSIONAL_FASTENER,
    MODEL_LIQUID_FINISH,
    MODEL_PIPE,
    STATUS_KNOWN,
    STATUS_NEEDS_IDENTITY,
    STATUS_NOISY,
    STATUS_POOR_DATA,
    STATUS_READY_EXISTING,
    STATUS_READY_GENERIC,
    build_family_signature,
    classify_family,
    format_text_report,
    run_batch_discover,
    select_high_impact,
    select_quick_wins,
)
from app.services.product_mapping.catalog_discover import SupplierProductRow
from app.services.product_mapping.rules import registered_codes


def _row(i: int, designation: str, *, mapped: bool = False) -> SupplierProductRow:
    return SupplierProductRow(
        id=i,
        supplier_reference=f"SKU-{i}",
        designation=designation,
        product_id=100 + i if mapped else None,
        correction_source="exact_rule" if mapped else None,
    )


def test_known_category_status():
    rows = [_row(1, "Plaque BA13 L.2,50 x l.1,20m x Ep.13mm")]
    sig = build_family_signature(rows)
    q = classify_family(
        family="PLAQUE_PLATRE",
        parent=None,
        sig=sig,
        known_category="PLAQUE_PLATRE",
    )
    assert q.status == STATUS_KNOWN
    assert q.candidate_model is None
    assert "PLAQUE_PLATRE" in q.reasons[0]


def test_fastener_candidate_ready_existing_model():
    rows = [
        _row(i, f"Vis agglo turbo 3,5 x {25 + i} mm 200 pièces")
        for i in range(20)
    ]
    sig = build_family_signature(rows)
    q = classify_family(family="VIS AGGLO", parent=None, sig=sig)
    assert q.status == STATUS_READY_EXISTING
    assert q.candidate_model == MODEL_DIMENSIONAL_FASTENER
    assert q.complexity == "XS"
    assert any("diameter_mm" in r for r in q.reasons)


def test_board_candidate_ready_existing_model():
    rows = [
        _row(i, f"Panneau OSB L.2500 x l.1250 x Ep.{10 + i % 5} mm")
        for i in range(18)
    ]
    sig = build_family_signature(rows)
    q = classify_family(family="PANNEAU OSB", parent=None, sig=sig)
    assert q.status == STATUS_READY_EXISTING
    assert q.candidate_model == MODEL_BOARD_PANEL


def test_pipe_candidate_ready_generic_features():
    rows = [
        _row(i, f"Tuyau PVC évacuation DN {40 + (i % 5) * 10} mm longueur 2 m")
        for i in range(20)
    ]
    sig = build_family_signature(rows)
    q = classify_family(family="TUYAU PVC", parent=None, sig=sig)
    assert q.status == STATUS_READY_GENERIC
    assert q.candidate_model == MODEL_PIPE
    assert q.complexity == "S"


def test_peinture_mur_needs_identity_not_ready_existing():
    rows = [
        _row(i, des)
        for i, des in enumerate(
            [
                "Peinture acrylique blanche mate 10 L",
                "Peinture glycéro satinée 2,5 L teinte",
                "Peinture murale beige 5 L",
                "Peinture sous-couche universelle 10 L base A",
                "Peinture acrylique blanche mate 10 L",
                "Peinture glycéro satinée 2,5 L",
                "Peinture murale beige 5 L",
                "Peinture sous-couche universelle 10 L",
                "Peinture acrylique blanche mate 10 L",
                "Peinture glycéro satinée 2,5 L",
                "Peinture murale beige 5 L",
                "Peinture sous-couche universelle 10 L",
                "Peinture acrylique blanche mate 10 L",
                "Peinture glycéro satinée 2,5 L",
                "Peinture murale beige 5 L",
                "Peinture testeur 75 ml",
            ],
            start=1,
        )
    ]
    sig = build_family_signature(rows)
    q = classify_family(family="PEINTURE", parent=None, sig=sig)
    assert q.candidate_model == MODEL_LIQUID_FINISH
    assert q.status == STATUS_NEEDS_IDENTITY
    assert q.status != STATUS_READY_EXISTING
    assert any("teinte/base/testeur" in r or "politique" in r for r in q.reasons)


def test_poor_data_family():
    rows = [_row(i, f"Article divers référence {i} sans mesure") for i in range(20)]
    sig = build_family_signature(rows)
    q = classify_family(family="DIVERS", parent=None, sig=sig)
    assert q.status == STATUS_POOR_DATA


def test_noisy_cluster():
    rows = [_row(i, f"Peinture acrylique 10 L n{i}") for i in range(20)]
    sig = build_family_signature(rows)
    q = classify_family(
        family="PRODUIT", parent=None, sig=sig, generic_cluster=True
    )
    assert q.status == STATUS_NOISY
    assert q.complexity == "L"


def test_under_min_size_excluded_from_run(session):
    supplier = Supplier(name="BATCH_MIN", source_type="file", source_key="batch-min")
    session.add(supplier)
    session.flush()
    # Moins de 15 SP → pas de famille Discover analysée (hors KNOWN vides).
    for i in range(5):
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                supplier_reference=f"m{i}",
                designation=f"Tuyau PVC DN {40 + i} mm",
                supplier_unit="pièce",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                active=True,
            )
        )
    session.commit()
    mapped_before = session.scalar(
        select(func.count()).select_from(SupplierProduct).where(
            SupplierProduct.product_id.is_not(None)
        )
    )

    report = run_batch_discover(
        session, supplier_name=supplier.name, min_size=15, top=10
    )
    session.rollback()

    # Seules les KNOWN (éventuellement 0 SP) apparaissent ; pas TUYAU.
    discover_families = [f for f in report.families if f.status != STATUS_KNOWN]
    assert all(f.count >= 15 for f in discover_families)
    mapped_after = session.scalar(
        select(func.count()).select_from(SupplierProduct).where(
            SupplierProduct.product_id.is_not(None)
        )
    )
    assert mapped_before == mapped_after
    assert report.product_id_hash_before == report.product_id_hash_after


def test_json_stable_and_deterministic():
    rows = [
        _row(i, f"Vis bois 4 x {30 + i} mm 100 pièces") for i in range(20)
    ]
    sig = build_family_signature(rows)
    a = classify_family(family="VIS BOIS", parent=None, sig=sig).to_dict()
    b = classify_family(family="VIS BOIS", parent=None, sig=sig).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["status"] == STATUS_READY_EXISTING
    assert set(a.keys()) >= {
        "family",
        "parent",
        "count",
        "status",
        "candidate_model",
        "reasons",
        "feature_coverage",
        "mapped_count",
        "examples",
        "complexity",
    }


def test_quick_wins_and_high_impact_ordering():
    fams = [
        classify_family(
            family="VIS A",
            parent=None,
            sig=build_family_signature(
                [_row(i, f"Vis agglo 4 x 40 mm {i}") for i in range(30)]
            ),
        ),
        classify_family(
            family="PEINTURE",
            parent=None,
            sig=build_family_signature(
                [_row(100 + i, f"Peinture acrylique {i % 3 + 1} L") for i in range(80)]
            ),
        ),
    ]
    wins = select_quick_wins(fams, top=5)
    assert wins and wins[0].status == STATUS_READY_EXISTING
    impact = select_high_impact(fams, top=5)
    assert impact[0].family == "PEINTURE"  # plus d'unmapped


def test_format_text_contains_sections():
    rows = [_row(i, f"Vis placo 3,5 x 25 mm {i}") for i in range(20)]
    q = classify_family(
        family="VIS",
        parent=None,
        sig=build_family_signature(rows),
    )
    from app.services.product_mapping.batch_discover import BatchDiscoverReport

    report = BatchDiscoverReport(
        supplier="TEST",
        min_size=15,
        total_sp=20,
        families_analysed=1,
        status_counts={STATUS_READY_EXISTING: 1},
        families=[q],
        quick_wins=[q],
        high_impact=[q],
        duration_ms=1,
        sp_per_sec=20.0,
        product_id_hash_before="abc",
        product_id_hash_after="abc",
    )
    text = format_text_report(report, top=5)
    assert "CATALOG BATCH DISCOVERY" in text
    assert "QUICK WINS" in text
    assert "HIGH IMPACT" in text


def test_batch_discover_recognizes_known_rules(session):
    """Les 4 CategoryRule enregistrées apparaissent en KNOWN_CATEGORY."""
    supplier = Supplier(name="BATCH_KNOWN", source_type="file", source_key="bk")
    session.add(supplier)
    session.flush()
    # Assez de peintures pour un cluster Discover (hors known).
    for i in range(20):
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                supplier_reference=f"p{i}",
                designation=f"Peinture acrylique blanche mate {(i % 4) + 1} L",
                supplier_unit="pièce",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                active=True,
            )
        )
    session.commit()
    report = run_batch_discover(
        session, supplier_name=supplier.name, min_size=15, top=10
    )
    known = {f.family for f in report.families if f.status == STATUS_KNOWN}
    assert set(registered_codes()).issubset(known)
    peinture = next((f for f in report.families if "PEINTURE" in f.family), None)
    if peinture is not None:
        assert peinture.status == STATUS_NEEDS_IDENTITY
        assert peinture.candidate_model == MODEL_LIQUID_FINISH
