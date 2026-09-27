"""Tests CATALOG DISCOVER V1 — cartographie lecture seule d'un catalogue.

Invariants vérifiés : familles connues séparées de la découverte, tokenisation
repliée/filtrée, clustering déterministe, aucun double comptage, aucune écriture.
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select

from app.models import Product, Supplier, SupplierProduct
from app.services.product_mapping.catalog_discover import (
    DEFAULT_MIN_CLUSTER_SIZE,
    bigrams,
    content_tokens,
    detect_features,
    format_text_report,
    run_catalog_discover,
    tokenize,
)

PLAQUE_DESIGNATION = "Plaque de plâtre BA13 Standard NF - L.2,50 x l.1,20m x Ep.13mm"
MULTI_RULE_DESIGNATION = (
    "Vis plaque de plâtre L.2,50 x l.1,20m x Ep.13mm 3,5 x 25 mm"
)
PEINTURES = (
    "Peinture acrylique blanche mate 10 L",
    "Peinture glycéro satinée 2,5 L",
    "Peinture murale beige 5 L",
    "Peinture sous-couche universelle 10 L",
)
TUYAUX = (
    "Tuyau PVC évacuation diamètre 100 mm",
    "Tuyau cuivre recuit 14 mm",
    "Tuyau PER gainé 16 mm",
    "Tuyau multicouche 20 mm",
)


# --- Fixtures utilitaires -------------------------------------------------

def _supplier(session, name: str = "DISCOVER_TEST") -> Supplier:
    supplier = Supplier(name=name, source_type="file", source_key="discover-test")
    session.add(supplier)
    session.flush()
    return supplier


def _add_sp(
    session,
    supplier: Supplier,
    reference: str,
    designation: str,
    *,
    product_id: int | None = None,
    correction_source: str | None = None,
) -> SupplierProduct:
    sp = SupplierProduct(
        product_id=product_id,
        supplier_id=supplier.id,
        supplier_reference=reference,
        designation=designation,
        supplier_unit="pièce",
        reference_quantity=Decimal("1"),
        packaging_quantity=Decimal("1"),
        correction_source=correction_source,
    )
    session.add(sp)
    session.flush()
    return sp


def _add_many(session, supplier, prefix: str, designations) -> list[SupplierProduct]:
    return [
        _add_sp(session, supplier, f"{prefix}{i:03d}", designation)
        for i, designation in enumerate(designations)
    ]


def _cluster(report, label: str):
    return next((c for c in report.clusters if c.label == label), None)


# --- Tokenisation ---------------------------------------------------------

def test_tokenization_folds_accents_and_min_length():
    tokens = tokenize("Plâtre ÉPAISSEUR Ép 13mm à")
    assert "platre" in tokens
    assert "epaisseur" in tokens
    # « Ép » et « à » sont sous la longueur minimale (3)
    assert "ep" not in tokens
    assert all(len(t) >= 3 for t in tokens)
    # Ponctuation séparatrice : « L.2,50 » ne produit aucun token utile
    assert tokenize("L.2,50 x l.1,20m") == ["20m"]
    assert tokenize("Rail-R48/2500") == ["rail", "r48", "2500"]


def test_stopwords_and_bare_numbers_excluded():
    tokens = content_tokens("Lot de 10 boîtes pour vis plaque avec type 200 pièces")
    assert tokens == ["vis", "plaque"]
    # Les nombres nus restent disponibles si on les demande explicitement
    assert "200" in content_tokens(
        "Lot de 200 vis plaque", keep_numbers=True
    )


def test_bigrams_are_consecutive_content_tokens():
    assert bigrams(content_tokens("Peinture acrylique blanche mate 10 L")) == [
        "peinture acrylique",
        "acrylique blanche",
        "blanche mate",
    ]
    # Les stopwords ne cassent pas la suite : « pot de peinture » → « pot peinture »
    assert "pot peinture" in bigrams(content_tokens("Pot de peinture blanche"))


def test_dimension_primitives_are_reused_not_reimplemented():
    features = detect_features(PLAQUE_DESIGNATION)
    assert features["dimensions_lxwxt_mm"] == "2500x1200x13"

    vis = detect_features("Vis placo 3,5 x 25 mm boîte de 200 pièces")
    assert vis["diameter_x_length_mm"] == "3.5x25"
    assert vis["piece_count"] == "200"

    assert detect_features("Rail R48 - 3 m NF")["bar_length_mm"] == "3000"
    assert detect_features("Pot de peinture blanche mate") == {}


# --- Étage A : familles connues -------------------------------------------

def test_known_category_recognized_and_excluded_from_discovery(session):
    supplier = _supplier(session)
    _add_sp(session, supplier, "plq001", PLAQUE_DESIGNATION)
    _add_many(session, supplier, "pei", PEINTURES)

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )

    stats = {k.code: k for k in report.known_categories}
    assert stats["PLAQUE_PLATRE"].classified == 1
    assert report.coverage.known_category_sp == 1
    # La plaque ne réapparaît pas en découverte
    assert _cluster(report, "PLAQUE") is None
    assert all("plaque" not in c.seed_token for c in report.clusters)


def test_multi_rule_designation_assigned_to_first_sorted_only(session):
    supplier = _supplier(session)
    _add_sp(session, supplier, "multi001", MULTI_RULE_DESIGNATION)

    report = run_catalog_discover(session, supplier_name=supplier.name, version=1)

    stats = {k.code: k for k in report.known_categories}
    # PLAQUE_PLATRE précède VIS_PLACO dans sorted(registered_codes())
    assert stats["PLAQUE_PLATRE"].classified == 1
    assert stats["VIS_PLACO"].classified == 0
    assert report.coverage.known_category_sp == 1
    assert sum(k.classified for k in report.known_categories) == 1


def test_known_category_mapped_and_unmapped_counters(session):
    supplier = _supplier(session)
    product = session.scalars(select(Product).limit(1)).first()
    assert product is not None
    _add_sp(
        session,
        supplier,
        "plq001",
        PLAQUE_DESIGNATION,
        product_id=product.id,
        correction_source="manual",
    )
    _add_sp(session, supplier, "plq002", PLAQUE_DESIGNATION.replace("BA13", "BA10"))

    report = run_catalog_discover(session, supplier_name=supplier.name, version=1)

    stats = {k.code: k for k in report.known_categories}["PLAQUE_PLATRE"]
    assert stats.classified == 2
    assert stats.mapped == 1
    assert stats.unmapped == 1
    assert stats.correction_sources == {"manual": 1}
    # Un SP non mappé tombe dans exactement un seau de raison
    assert (
        stats.exact
        + stats.high
        + stats.review
        + stats.ambiguous
        + stats.no_pmc_product
        + stats.insufficient_data
        == stats.unmapped
    )


# --- Étage B : découverte -------------------------------------------------

def test_unknown_designations_form_a_discovery_cluster(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )

    cluster = _cluster(report, "PEINTURE")
    assert cluster is not None
    assert cluster.sp_count == 4
    assert cluster.rule_exists is False
    assert cluster.existing_pmc_candidates is None
    assert cluster.label_cohesion == 1.0
    assert cluster.top_tokens[0] == ("peinture", 4)
    assert len(cluster.examples) == 4
    assert report.coverage.known_category_sp == 0


def test_two_distinct_families_produce_two_clusters(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)
    _add_many(session, supplier, "tuy", TUYAUX)

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )

    assert {c.label for c in report.clusters} == {"PEINTURE", "TUYAU"}
    assert report.coverage.cluster_count == 2
    assert report.coverage.discovery_cluster_sp == 8
    assert report.coverage.residual == 0
    assert all(c.sp_count == 4 for c in report.clusters)


def test_cluster_mapped_unmapped_and_prioritization(session):
    supplier = _supplier(session)
    product = session.scalars(select(Product).limit(1)).first()
    assert product is not None
    rows = _add_many(session, supplier, "pei", PEINTURES)
    rows[0].product_id = product.id
    rows[1].product_id = product.id
    session.flush()

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )

    cluster = _cluster(report, "PEINTURE")
    assert cluster is not None
    assert (cluster.mapped, cluster.unmapped, cluster.sp_count) == (2, 2, 4)
    priority = report.prioritization[0]
    assert priority.family == "PEINTURE"
    assert priority.unmapped == 2
    assert priority.mapped == 2
    assert priority.rule_exists is False
    # Priorisation triée par non mappés décroissants
    assert [p.unmapped for p in report.prioritization] == sorted(
        (p.unmapped for p in report.prioritization), reverse=True
    )


def test_below_threshold_sps_fall_into_residual(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)
    _add_sp(session, supplier, "iso001", "Isolant laine minérale 100 mm")
    _add_sp(session, supplier, "iso002", "Colle carrelage grise 25 kg")

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )

    assert report.coverage.cluster_count == 1
    assert report.residual.sp_count == 2
    assert {e.sku for e in report.residual.examples} == {"iso001", "iso002"}
    assert report.residual.top_tokens


def test_colour_tokens_are_not_cluster_seeds(session):
    supplier = _supplier(session)
    _add_many(
        session,
        supplier,
        "blc",
        (
            "Radiateur électrique programmable blanc 2000 W",
            "Fenêtre oscillo-battante blanc 100 x 120 cm",
            "Meuble sous vasque blanc 80 cm",
            "Volet roulant blanc 120 cm",
        ),
    )

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )

    # « blanc » est présent dans les 4 désignations mais reste un qualificatif
    assert _cluster(report, "BLANC") is None
    assert report.coverage.cluster_count == 0
    assert report.residual.sp_count == 4


def test_clustering_is_deterministic(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)
    _add_many(session, supplier, "tuy", TUYAUX)

    first = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    ).to_dict()
    second = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    ).to_dict()

    for payload in (first, second):
        payload["coverage"].pop("duration_ms")
        payload["coverage"].pop("sp_per_sec")
    assert first == second


def test_no_double_counting_across_stages(session):
    supplier = _supplier(session)
    _add_sp(session, supplier, "plq001", PLAQUE_DESIGNATION)
    _add_sp(session, supplier, "oss001", "Lot de 10 montants M4835 NF - 48 x 2490 mm")
    _add_many(session, supplier, "pei", PEINTURES)
    _add_many(session, supplier, "tuy", TUYAUX)
    _add_sp(session, supplier, "res001", "Colle carrelage grise 25 kg")

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )
    cov = report.coverage

    assert cov.total == 11
    assert cov.known_category_sp + cov.discovery_cluster_sp + cov.residual == cov.total
    assert sum(c.sp_count for c in report.clusters) == cov.discovery_cluster_sp
    assert sum(k.classified for k in report.known_categories) == cov.known_category_sp
    # Un SP n'apparaît que dans un seul cluster
    example_ids = [e.id for c in report.clusters for e in c.examples]
    assert len(example_ids) == len(set(example_ids))


# --- Sorties / garde-fous -------------------------------------------------

def test_json_keys_are_stable_and_unicode_preserved(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )
    payload = report.to_dict()

    assert list(payload) == [
        "version",
        "supplier",
        "supplier_ids",
        "min_cluster_size",
        "top_clusters",
        "example_limit",
        "coverage",
        "known_categories",
        "prioritization",
        "clusters",
        "residual",
        "product_id_hash_before",
        "product_id_hash_after",
    ]
    assert list(payload["coverage"]) == [
        "total",
        "known_category_sp",
        "discovery_cluster_sp",
        "residual",
        "cluster_count",
        "duration_ms",
        "sp_per_sec",
    ]
    assert list(payload["clusters"][0]) == [
        "label",
        "seed_token",
        "sp_count",
        "mapped",
        "unmapped",
        "top_tokens",
        "top_bigrams",
        "label_cohesion",
        "top3_cohesion",
        "technical_feature_sp",
        "technical_feature_coverage",
        "features",
        "examples",
        "rule_exists",
        "existing_pmc_candidates",
    ]

    dumped = json.dumps(payload, ensure_ascii=False)
    assert "glycéro" in dumped
    assert json.loads(dumped) == payload


def test_text_report_is_human_readable(session):
    supplier = _supplier(session)
    _add_many(session, supplier, "pei", PEINTURES)

    text = format_text_report(
        run_catalog_discover(
            session, supplier_name=supplier.name, min_cluster_size=4, version=1
        )
    )
    assert "CATALOG DISCOVER V1" in text
    assert "PEINTURE" in text
    assert "Résidu non classé" in text


def test_discover_never_writes_product_id(session):
    supplier = _supplier(session)
    product = session.scalars(select(Product).limit(1)).first()
    assert product is not None
    _add_sp(session, supplier, "plq001", PLAQUE_DESIGNATION)
    _add_sp(session, supplier, "plq002", PLAQUE_DESIGNATION, product_id=product.id)
    _add_many(session, supplier, "pei", PEINTURES)

    before = dict(
        session.execute(
            select(SupplierProduct.id, SupplierProduct.product_id)
        ).all()
    )
    products_before = len(session.scalars(select(Product.id)).all())

    report = run_catalog_discover(
        session, supplier_name=supplier.name, min_cluster_size=4, version=1
    )

    after = dict(
        session.execute(
            select(SupplierProduct.id, SupplierProduct.product_id)
        ).all()
    )
    assert after == before
    assert len(session.scalars(select(Product.id)).all()) == products_before
    assert report.product_id_hash_before == report.product_id_hash_after


def test_empty_catalog_is_reported_without_crashing(session):
    supplier = _supplier(session, name="DISCOVER_EMPTY")

    report = run_catalog_discover(session, supplier_name=supplier.name, version=1)

    assert report.min_cluster_size == DEFAULT_MIN_CLUSTER_SIZE
    assert report.coverage.total == 0
    assert report.coverage.cluster_count == 0
    assert report.coverage.residual == 0
    assert report.clusters == []
    assert report.residual.sp_count == 0
    assert "TOTAL SupplierProduct     : 0" in format_text_report(report)


def test_unknown_supplier_yields_empty_report(session):
    report = run_catalog_discover(
        session, supplier_name="FOURNISSEUR_INEXISTANT", version=1
    )
    assert report.supplier_ids == []
    assert report.coverage.total == 0


def test_cli_dispatch_keeps_legacy_category_interface():
    from tools.product_mapping import parse_args, parse_catalog_discover_args

    legacy = parse_args(["--supplier", "BRICO_DEPOT", "--category", "VIS_PLACO", "--all"])
    assert legacy.category == "VIS_PLACO"
    assert legacy.all_candidates is True

    discover = parse_catalog_discover_args(["--supplier", "BRICO_DEPOT"])
    assert discover.supplier == "BRICO_DEPOT"
    assert discover.format == "text"
    assert discover.min_cluster_size == DEFAULT_MIN_CLUSTER_SIZE
    assert discover.discover_version == 2
    assert parse_catalog_discover_args(["--format", "json"]).supplier is None
