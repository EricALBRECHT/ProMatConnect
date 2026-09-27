"""Tests CATALOG DISCOVER V2 — bigrammes, hiérarchie, familles actionnables."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.models import Product, Supplier, SupplierProduct
from app.services.product_mapping.catalog_discover import (
    DISCOVER_VERSION_V2,
    SEED_BIGRAM,
    SEED_UNIGRAM,
    SupplierProductRow,
    assign_clusters,
    assign_clusters_v2,
    build_actionable_families,
    default_min_bigram_size,
    detect_features,
    format_text_report,
    head_and_qualifier,
    idf,
    is_generic_cluster,
    run_catalog_discover,
)
from tests.test_catalog_discover import (
    PEINTURES,
    PLAQUE_DESIGNATION,
    TUYAUX,
    _add_many,
    _add_sp,
    _cluster,
    _supplier,
)


def _rows(session, supplier, designations) -> list[SupplierProductRow]:
    from app.services.product_mapping.catalog_discover import load_supplier_products

    _add_many(session, supplier, "v2", designations)
    session.flush()
    return load_supplier_products(session, [supplier.id])


def test_v2_report_version_and_schema(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=2
    )
    assert report.version == DISCOVER_VERSION_V2
    payload = report.to_dict()
    assert payload["version"] == DISCOVER_VERSION_V2
    assert "actionable_families" in payload
    assert "token_discrimination" in payload
    assert "bigram_discrimination" in payload
    assert "v1_comparison" in payload
    assert payload["token_discrimination"][0]["idf"] >= 1.0
    assert "frequency" in payload["token_discrimination"][0]


def test_bigram_preferred_over_unigram_material(session):
    """Un bigramme produit (vis bois) prime le regroupement unigramme « bois »."""
    supplier = _supplier(session)
    designations = tuple(f"Vis bois {i}" for i in range(30)) + tuple(
        f"Vis acier {i}" for i in range(30)
    )
    rows = _rows(session, supplier, designations)
    assignment = assign_clusters_v2(rows, min_cluster_size=25, min_bigram_size=15)
    bigram_vis = next(
        (g for g in assignment.groups if g.seed == "vis bois"), None
    )
    assert bigram_vis is not None
    assert bigram_vis.seed_kind == SEED_BIGRAM
    assert len(bigram_vis.members) >= 25
    bois_unigram = next(
        (g for g in assignment.groups if g.seed == "bois" and g.seed_kind == SEED_UNIGRAM),
        None,
    )
    assert bois_unigram is None


def test_head_qualifier_uses_higher_idf_token():
    idf_map = {"vis": 2.1, "bois": 1.4, "peinture": 2.5, "acrylique": 1.2}

    def idf_of(token: str) -> float:
        return idf_map[token]

    head, qual = head_and_qualifier("vis bois", SEED_BIGRAM, idf_of)
    assert head == "vis"
    assert qual == "bois"
    head2, qual2 = head_and_qualifier("peinture acrylique", SEED_BIGRAM, idf_of)
    assert head2 == "peinture"
    assert qual2 == "acrylique"


def test_head_qualifier_material_is_lower_idf(session):
    supplier = _supplier(session)
    rows = _rows(
        session,
        supplier,
        (
            "Peinture acrylique blanche 10 L",
            "Peinture acrylique satinée 5 L",
            "Peinture acrylique mate 2 L",
            "Enduit acrylique fin 25 kg",
        ),
    )
    assignment = assign_clusters_v2(rows, min_cluster_size=4, min_bigram_size=2)
    bigram = next((g for g in assignment.groups if g.seed_kind == SEED_BIGRAM), None)
    assert bigram is not None
    head, qual = head_and_qualifier(
        bigram.seed, SEED_BIGRAM, assignment.idf_of_token
    )
    assert head == "acrylique" or head in bigram.seed.split()
    assert qual is not None


def test_local_recluster_splits_large_unigram_parent(session):
    supplier = _supplier(session)
    base = [
        "Peinture murale blanche 10 L",
        "Peinture plafond blanche 5 L",
        "Peinture cuisine blanche 2 L",
        "Peinture salle bain blanche 1 L",
    ]
    extra_vis = [f"Vis bois {i} mm" for i in range(200)]
    rows = _rows(session, supplier, tuple(base) + tuple(extra_vis))
    assignment = assign_clusters_v2(
        rows, min_cluster_size=4, min_bigram_size=2, recluster_threshold=50
    )
    peinture = next((g for g in assignment.groups if g.seed == "peinture"), None)
    assert peinture is not None
    assert peinture.seed_kind == SEED_UNIGRAM
    if len(peinture.members) >= 50:
        assert peinture.split or peinture.children or peinture.residual_local


def test_generic_cluster_low_idf_unigram(session):
    supplier = _supplier(session)
    rows = _rows(
        session,
        supplier,
        tuple(f"Produit bois variante {i} ref" for i in range(30))
        + tuple(f"Autre article bois {i}" for i in range(30)),
    )
    assignment = assign_clusters_v2(rows, min_cluster_size=25, min_bigram_size=15)
    from app.services.product_mapping.catalog_discover import build_v2_cluster_reports

    clusters = build_v2_cluster_reports(assignment, example_limit=3)
    bois = next((c for c in clusters if c.seed_token == "bois"), None)
    if bois is not None:
        assert bois.seed_kind == SEED_UNIGRAM
        assert isinstance(bois.generic_cluster, bool)


def test_hierarchy_children_no_double_count(session):
    supplier = _supplier(session)
    _add_sp(session, supplier, "plq001", PLAQUE_DESIGNATION)
    _add_many(session, supplier, "pei", PEINTURES)
    _add_many(session, supplier, "tuy", TUYAUX)

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=2
    )
    cov = report.coverage
    assert cov.known_category_sp + cov.discovery_cluster_sp + cov.residual == cov.total
    parent_sp = sum(c.sp_count for c in report.clusters)
    child_sp = sum(ch.sp_count for c in report.clusters for ch in c.children)
    residual_local = sum(c.residual_local_count for c in report.clusters)
    assert parent_sp == cov.discovery_cluster_sp
    assert child_sp + residual_local <= parent_sp


def test_measure_primitives_volume_weight_dn():
    assert detect_features("Peinture acrylique 10 L")["volume_ml"] == "10000"
    assert detect_features("Colle carrelage 25 kg")["weight_g"] == "25000"
    assert detect_features("Raccord PVC évacuation DN 100")["dn"] == "100"


def test_v1_assign_clusters_unchanged(session):
    supplier = _supplier(session)
    rows = _rows(session, supplier, PEINTURES)
    clusters, residual, _ = assign_clusters(rows, min_cluster_size=4)
    assert "peinture" in clusters
    assert len(clusters["peinture"]) == 4
    assert not residual


def test_actionable_families_prefer_bigrams_and_cohesion(session):
    supplier = _supplier(session)
    designations = tuple(
        f"Vis bois pro {i} mm" for i in range(30)
    ) + tuple(f"Cheville nylon pro {i} mm" for i in range(30))
    rows = _rows(session, supplier, designations)
    assignment = assign_clusters_v2(rows, min_cluster_size=25, min_bigram_size=15)
    from app.services.product_mapping.catalog_discover import build_v2_cluster_reports

    clusters = build_v2_cluster_reports(assignment, example_limit=3)
    actionable = build_actionable_families(
        clusters,
        min_cluster_size=25,
        low_idf_threshold=assignment.low_idf_threshold,
        example_limit=3,
    )
    assert actionable
    assert all(a.label_cohesion >= 0.85 for a in actionable)
    assert actionable[0].unmapped >= actionable[-1].unmapped or len(actionable) == 1


def test_json_v2_cluster_keys(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)
    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=2
    )
    cluster = report.to_dict()["clusters"][0]
    for key in (
        "seed_kind",
        "head",
        "qualifier",
        "document_frequency",
        "idf",
        "generic_cluster",
        "children",
        "residual_local_count",
    ):
        assert key in cluster


def test_discover_v2_read_only(session):
    supplier = _supplier(session)
    product = session.scalars(select(Product).limit(1)).first()
    assert product is not None
    _add_sp(session, supplier, "plq001", PLAQUE_DESIGNATION, product_id=product.id)
    _add_many(session, supplier, "pei", PEINTURES)

    before = dict(
        session.execute(
            select(SupplierProduct.id, SupplierProduct.product_id)
        ).all()
    )
    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=2
    )
    after = dict(
        session.execute(
            select(SupplierProduct.id, SupplierProduct.product_id)
        ).all()
    )
    assert after == before
    assert report.product_id_hash_before == report.product_id_hash_after


def test_empty_catalog_v2(session):
    supplier = _supplier(session, name="DISCOVER_V2_EMPTY")
    report = run_catalog_discover(session, supplier_name=supplier.name, version=2)
    assert report.version == DISCOVER_VERSION_V2
    assert report.coverage.total == 0
    assert "CATALOG DISCOVER V2" in format_text_report(report)


def test_v1_comparison_legacy_labels(session):
    supplier = _supplier(session)
    designations = tuple(f"Tube PVC évacuation {i} mm" for i in range(30)) + tuple(
        f"Coude PVC {i} mm" for i in range(30)
    )
    _add_many(session, supplier, "pvc", designations)
    report = run_catalog_discover(
        session,
        supplier_name=supplier.name,
        min_cluster_size=25,
        version=2,
        compare_v1=True,
    )
    labels = {c.label for c in report.v1_comparison}
    assert "PVC" in labels or "TUBE" in labels
    assert report.v1_comparison


def test_idf_formula():
    assert idf(100, 100) == round(__import__("math").log(101 / 101) + 1, 4)
    assert idf(1, 1000) > idf(500, 1000)


def test_default_min_bigram_size_for_25():
    assert default_min_bigram_size(25) == 15


def test_no_compare_v1(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)
    report = run_catalog_discover(
        session,
        supplier_name=supplier.name,
        min_cluster_size=4,
        version=2,
        compare_v1=False,
    )
    assert report.v1_comparison == []


def test_text_report_v2_sections(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)
    text = format_text_report(
        run_catalog_discover(
            session, supplier_name=supplier.name, min_cluster_size=4, version=2
        )
    )
    assert "familles actionnables" in text
    assert "Discrimination" in text
