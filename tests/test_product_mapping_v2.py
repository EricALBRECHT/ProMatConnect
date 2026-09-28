"""Tests V2 — primitives partagées, CategoryRule, matcher générique, pipeline.

Vérifie que l'industrialisation (règles déclaratives) conserve les invariants :
UNKNOWN ≠ false, normalisation explicite (jamais d'arrondi), mapping existant
intouchable.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    CORRECTION_SOURCE_EXACT_RULE,
    CORRECTION_SOURCES_PROTECTED,
    KIND_MONTANT,
    PROPOSAL_EXACT,
    PROPOSAL_REVIEW,
    PROPOSAL_UNMAPPED,
)
from app.services.bricodepot_catalog_import import resolve_brico_supplier
from app.services.product_mapping import generic_matcher, pipeline
from app.services.product_mapping.feature_set import (
    SOURCE_TEXT,
    SOURCE_UNKNOWN,
    FeatureSet,
    apply_normalizations,
    feature_from_text,
    feature_set_from_extraction,
    features_from_attrs,
)
from app.services.product_mapping.primitives import (
    extract_bar_length_mm,
    extract_diameter_length_mm,
    extract_dimensions_mm,
    extract_lot_quantity,
    extract_metal_profile,
    fold,
    to_mm,
)
from app.services.product_mapping.rules import get_rule, registered_codes
from app.services.product_mapping.rules.base import (
    CategoryRule,
    NormalizationSpec,
    apply_normalization_specs,
)
from app.services.product_mapping.identity_models import (
    BOARD_PANEL,
    DIMENSIONAL_FASTENER,
    LINEAR_PROFILE,
)
from app.services.product_mapping.rules.ossature_placo import (
    NOMINAL_LENGTH_MAP,
    OSSATURE_PLACO_RULE,
)
from app.services.product_mapping.rules.plaque_platre import PLAQUE_PLATRE_RULE
from app.services.product_mapping.rules.vis_agglo import VIS_AGGLO_RULE
from app.services.product_mapping.rules.vis_placo import VIS_PLACO_RULE
from app.services.product_mapping.vis_extractor import extract_vis_placo
from app.services.product_mapping.service import ProductMappingService

# --- Primitives -----------------------------------------------------------

def test_primitives_shared_by_both_categories():
    assert fold("Plâtre Épaisseur") == "platre epaisseur"
    assert to_mm(Decimal("2.5"), "m") == 2500
    assert to_mm(Decimal("1.25"), "cm") == 13  # half-up
    assert to_mm(Decimal("2.5"), None, bare_small_as_meters=True) == 2500
    assert to_mm(Decimal("2500"), None) == 2500
    # Hors bornes métier → None (pas de valeur inventée)
    assert to_mm(Decimal("300"), "mm", min_mm=500, max_mm=12000) is None
    assert extract_dimensions_mm("L.2,50 x l.1,20m x Ep.13mm") == (2500, 1200, 13)
    assert extract_bar_length_mm("Rail R48 - 3 m NF") == 3000
    assert extract_metal_profile("montants M4835 NF") == "M48"
    assert extract_lot_quantity("Lot de 10 montants M4835") == 10
    assert extract_lot_quantity("Montant M48 seul") is None
    assert extract_diameter_length_mm("Vis 3,5 x 25 mm") == (3.5, 25)
    assert extract_diameter_length_mm("Rail R48 - 3 m NF") == (None, None)


# --- CategoryRule / identité ---------------------------------------------

def test_identity_keys_declared_by_rule():
    assert PLAQUE_PLATRE_RULE.identity_keys == (
        "length_mm",
        "width_mm",
        "thickness_mm",
        "type",
    )
    assert OSSATURE_PLACO_RULE.identity_keys == ("kind", "profile", "nominal_length_mm")
    # L'identité extraite est projetée sur la clé produit correspondante
    assert OSSATURE_PLACO_RULE.product_key("nominal_length_mm") == "length_mm"
    assert PLAQUE_PLATRE_RULE.product_key("thickness_mm") == "thickness_mm"


def test_rule_registry_exposes_every_category():
    assert registered_codes() == (
        "BOULON",
        "CHEVILLE_METAL",
        "CHEVILLE_NYLON",
        "CORNIERE_PVC",
        "FER_BETON",
        "GOUJON",
        "OSSATURE_PLACO",
        "PANNEAU_AGGLO",
        "PANNEAU_CP",
        "PANNEAU_MDF",
        "PANNEAU_OSB",
        "PLAQUE_PLATRE",
        "ROND_ACIER",
        "TIREFOND",
        "TUBE_ROND_ACIER",
        "VIS_AGGLO",
        "VIS_BETON",
        "VIS_BOIS",
        "VIS_MULTI",
        "VIS_PLACO",
        "VIS_TERRASSE",
        "VIS_TOLE",
    )
    assert get_rule("OSSATURE_PLACO") is OSSATURE_PLACO_RULE
    assert get_rule("PLAQUE_PLATRE") is PLAQUE_PLATRE_RULE
    assert get_rule("VIS_PLACO") is VIS_PLACO_RULE


def test_identity_complete_requires_every_key():
    complete = {"kind": KIND_MONTANT, "profile": "M48", "nominal_length_mm": 2500}
    assert OSSATURE_PLACO_RULE.identity_complete(complete) is True
    assert (
        OSSATURE_PLACO_RULE.identity_complete({**complete, "profile": None}) is False
    )


# --- Normalisation explicite ---------------------------------------------

def test_explicit_normalization_table_not_rounding():
    attrs = apply_normalization_specs(
        {"length_mm": 2490}, OSSATURE_PLACO_RULE.normalizations
    )
    assert attrs["length_mm"] == 2490  # source jamais modifiée
    assert attrs["nominal_length_mm"] == 2500

    assert (
        apply_normalization_specs({"length_mm": 2990}, OSSATURE_PLACO_RULE.normalizations)
    )["nominal_length_mm"] == 3000

    # Hors table : traversée inchangée — surtout pas d'arrondi générique
    for raw in (2480, 2510, 2980, 3010, 5300):
        out = apply_normalization_specs(
            {"length_mm": raw}, OSSATURE_PLACO_RULE.normalizations
        )
        assert out["nominal_length_mm"] == raw, raw

    assert NOMINAL_LENGTH_MAP == {2490: 2500, 2500: 2500, 2990: 3000, 3000: 3000}


def test_normalization_of_unknown_stays_unknown():
    out = apply_normalization_specs(
        {"length_mm": None}, OSSATURE_PLACO_RULE.normalizations
    )
    assert out["nominal_length_mm"] is None


def test_normalization_spec_is_generic():
    spec = NormalizationSpec(
        source_key="diameter_tenths",
        target_key="nominal_diameter_tenths",
        mapping={4: 35},
    )
    out = apply_normalization_specs({"diameter_tenths": 4}, (spec,))
    assert out == {"diameter_tenths": 4, "nominal_diameter_tenths": 35}


# --- FeatureSet : UNKNOWN ≠ false ----------------------------------------

def test_unknown_is_null_never_false():
    values = features_from_attrs(
        {"kind": "RAIL", "profile": None, "hydrofuge": False, "length_mm": 0}
    )
    assert values["profile"].normalized is None
    assert values["profile"].source == SOURCE_UNKNOWN
    assert values["profile"].is_unknown is True
    # Un false explicite reste un false — il n'est pas confondu avec l'absence
    assert values["hydrofuge"].normalized is False
    assert values["hydrofuge"].source == SOURCE_TEXT
    assert values["length_mm"].normalized == 0

    fs = FeatureSet(category_code="X", values=values, classified=True)
    assert fs.get("profile") is None
    assert fs.as_attrs()["profile"] is None
    assert fs.raw_attrs()["kind"] == "RAIL"

    assert feature_from_text(None).source == SOURCE_UNKNOWN
    assert feature_from_text("RAIL").normalized == "RAIL"


def test_apply_normalizations_on_feature_values():
    values = features_from_attrs({"length_mm": 2490})
    apply_normalizations(values, OSSATURE_PLACO_RULE.normalizations)
    assert values["length_mm"].normalized == 2490
    assert values["nominal_length_mm"].raw == 2490
    assert values["nominal_length_mm"].normalized == 2500
    assert values["nominal_length_mm"].source == SOURCE_TEXT

    unknown = features_from_attrs({"length_mm": None})
    apply_normalizations(unknown, OSSATURE_PLACO_RULE.normalizations)
    assert unknown["nominal_length_mm"].normalized is None
    assert unknown["nominal_length_mm"].source == SOURCE_UNKNOWN


# --- Matcher générique ----------------------------------------------------

_M48_2500 = (1, "PMC-MONTANT-M48-2500", {"profile": "M48", "length_mm": 2500}, "Montants")


def test_generic_exact_via_nominal_length():
    extracted = {
        "kind": KIND_MONTANT,
        "profile": "M48",
        "length_mm": 2490,
        "nominal_length_mm": 2500,
    }
    m = generic_matcher.best_match(OSSATURE_PLACO_RULE, extracted, [_M48_2500])
    assert m.status == PROPOSAL_EXACT
    assert m.reason == "EXACT"
    assert m.product_code == "PMC-MONTANT-M48-2500"


def test_generic_no_tolerance_2480():
    extracted = {
        "kind": KIND_MONTANT,
        "profile": "M48",
        "length_mm": 2480,
        "nominal_length_mm": 2480,
    }
    m = generic_matcher.best_match(OSSATURE_PLACO_RULE, extracted, [_M48_2500])
    assert m.status == PROPOSAL_UNMAPPED
    assert m.reason == "NO_PMC_PRODUCT"


def test_generic_missing_identity_is_insufficient_not_exact():
    m = generic_matcher.best_match(
        OSSATURE_PLACO_RULE,
        {"kind": KIND_MONTANT, "profile": None, "nominal_length_mm": 2500},
        [_M48_2500],
    )
    assert m.status == PROPOSAL_UNMAPPED
    assert m.reason == "INSUFFICIENT_DATA"
    assert m.product_id is None

    # Plaque : identité incomplète (type UNKNOWN) → jamais EXACT
    plaque = generic_matcher.score_against_product(
        PLAQUE_PLATRE_RULE,
        {"length_mm": 2500, "width_mm": 1200, "thickness_mm": 13, "type": None},
        product_id=1,
        product_code="PMC-BA13-STD-2500X1200",
        product_attrs={
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "standard",
        },
    )
    assert plaque.status == PROPOSAL_REVIEW
    assert plaque.status != PROPOSAL_EXACT


def test_generic_ambiguous_two_exact_candidates():
    extracted = {
        "kind": KIND_MONTANT,
        "profile": "M48",
        "length_mm": 2500,
        "nominal_length_mm": 2500,
    }
    m = generic_matcher.best_match(
        OSSATURE_PLACO_RULE,
        extracted,
        [
            _M48_2500,
            (2, "PMC-MONTANT-M48-2500-BIS", {"profile": "M48", "length_mm": 2500}, "Montants"),
        ],
    )
    assert m.reason == "AMBIGUOUS"
    assert m.status == PROPOSAL_REVIEW


def test_generic_matcher_stamps_rule_algorithm_version():
    m = generic_matcher.best_match(
        OSSATURE_PLACO_RULE,
        {"kind": KIND_MONTANT, "profile": "M48", "nominal_length_mm": 2500},
        [_M48_2500],
    )
    assert m.algorithm_version == OSSATURE_PLACO_RULE.algorithm_version


# --- Pipeline -------------------------------------------------------------

def test_pipeline_already_mapped_never_recomputed(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.flush()
    product = session.scalar(
        select(Product).where(Product.code == "PMC-MONTANT-M48-2500")
    )
    assert product is not None
    supplier = resolve_brico_supplier(session)
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=supplier.id,
        supplier_reference="v2already001",
        designation="Lot de 10 montants M4835 NF - 48 x 2490 mm",
        supplier_unit="lot",
        reference_quantity=Decimal("10"),
        packaging_quantity=Decimal("10"),
        correction_source="manual",
    )
    session.add(sp)
    session.flush()

    run = pipeline.run_category(
        session,
        OSSATURE_PLACO_RULE,
        supplier_name=supplier.name,
        limit=None,
        all_candidates=True,
    )
    session.refresh(sp)
    assert sp.product_id == product.id
    assert sp.correction_source == "manual"
    assert run.already_mapped >= 1
    item = next(i for i in run.items if i.sp.id == sp.id)
    assert item.outcome == pipeline.OUTCOME_ALREADY_MAPPED
    assert item.match is None  # aucun recalcul sur un mapping existant
    assert item.attrs["length_mm"] == 2490
    assert item.attrs["nominal_length_mm"] == 2500


def test_pipeline_exact_application_protected_by_constants(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    supplier = resolve_brico_supplier(session)
    sp = SupplierProduct(
        product_id=None,
        supplier_id=supplier.id,
        supplier_reference="v2exact001",
        designation="Plaque de plâtre BA13 Standard NF - L.2,50 x l.1,20m x Ep.13mm",
        supplier_unit="La pièce",
        reference_quantity=Decimal("1"),
        packaging_quantity=Decimal("1"),
        correction_source="import",
    )
    session.add(sp)
    session.flush()

    preview = pipeline.apply_exact_for_rule(
        session, PLAQUE_PLATRE_RULE, supplier_name=supplier.name, dry_run=True
    )
    session.refresh(sp)
    assert preview.applied == 0
    assert sp.product_id is None

    applied = ProductMappingService(session).apply_exact_plaque_platre(
        supplier_name=supplier.name, dry_run=False
    )
    session.flush()
    session.refresh(sp)
    assert applied.applied >= 1
    assert sp.correction_source == CORRECTION_SOURCE_EXACT_RULE
    assert CORRECTION_SOURCE_EXACT_RULE in CORRECTION_SOURCES_PROTECTED


def test_pipeline_is_exact_applicable_refuses_unknown_identity():
    class _SP:
        product_id = None

    fake_exact = generic_matcher.MatchResult(
        status=PROPOSAL_EXACT,
        product_id=1,
        product_code="PMC-MONTANT-M48-2500",
        score=1.0,
        breakdown={},
        reason="EXACT",
    )
    assert (
        pipeline.is_exact_applicable(
            OSSATURE_PLACO_RULE,
            sp=_SP(),
            extraction_attrs={
                "kind": KIND_MONTANT,
                "profile": None,
                "nominal_length_mm": 2500,
            },
            match=fake_exact,
        )
        is False
    )
    assert (
        pipeline.is_exact_applicable(
            OSSATURE_PLACO_RULE,
            sp=_SP(),
            extraction_attrs={
                "kind": KIND_MONTANT,
                "profile": "M48",
                "nominal_length_mm": 2500,
            },
            match=fake_exact,
        )
        is True
    )


# --- VIS_PLACO : famille ajoutée en configuration seule ------------------

def test_vis_placo_added_without_dedicated_matcher_or_pipeline():
    """Ajouter une famille = une CategoryRule + un extracteur de primitives."""
    rule = VIS_PLACO_RULE
    assert rule.identity is DIMENSIONAL_FASTENER
    assert rule.identity_keys == ("type", "diameter_mm", "length_mm")
    assert rule.product_key("diameter_mm") == "diameter_mm"
    # Aucune table de correspondance : les diamètres fournisseur sont déjà nominaux
    assert rule.normalizations == ()

    extraction = extract_vis_placo(
        "Seau de 1000 vis plaque de plâtre tête trompette Philips 3,5 x 25 mm"
    )
    assert extraction.classified is True
    assert extraction.attributes["diameter_mm"] == 3.5
    assert extraction.attributes["length_mm"] == 25
    assert extraction.attributes["packaging_qty"] == 1000

    # Le pipeline générique adapte l'extraction sans code dédié à la catégorie
    fs = feature_set_from_extraction(extraction, norms=rule.normalizations)
    assert isinstance(fs, FeatureSet)
    assert fs.as_attrs()["type"] == "placo"

    # Et le matcher générique consomme l'identité telle quelle
    m = generic_matcher.best_match(
        rule,
        fs.as_attrs(),
        [
            (
                1,
                "PMC-VIS-PLACO-35X25",
                {"type": "placo", "diameter_mm": 3.5, "length_mm": 25},
                "Vis placo",
            )
        ],
    )
    assert m.status == PROPOSAL_EXACT
    assert m.algorithm_version == "vis_match.v1"


# --- IdentityModel V2.5 ---------------------------------------------------

def test_rules_share_identity_models():
    assert VIS_PLACO_RULE.identity is DIMENSIONAL_FASTENER
    assert VIS_AGGLO_RULE.identity is DIMENSIONAL_FASTENER
    assert OSSATURE_PLACO_RULE.identity is LINEAR_PROFILE
    assert PLAQUE_PLATRE_RULE.identity is BOARD_PANEL
    assert "packaging_qty" in DIMENSIONAL_FASTENER.ignored_for_identity
    assert "head" in DIMENSIONAL_FASTENER.ignored_for_identity


def test_synthetic_test_fastener_reuses_dimensional_fastener_without_new_matcher():
    """Nouvelle famille proche = CategoryRule légère + IdentityModel existant.

    Aucun matcher dédié, aucun pipeline dédié, pas d'enregistrement métier DB.
    """
    rule = CategoryRule(
        code="TEST_FASTENER",
        category_name="Test fastener (synthétique)",
        identity=DIMENSIONAL_FASTENER,
        pmc_code_prefixes=("PMC-TEST-FAST-",),
        algorithm_version="test_fastener.v0",
        extractor_version="test_fastener.v0",
    )
    # Non enregistré dans le registre métier
    assert "TEST_FASTENER" not in registered_codes()
    assert rule.identity_keys == DIMENSIONAL_FASTENER.identity_keys
    assert rule.hierarchy_keys == ("type", "diameter_mm")

    attrs = {
        "type": "test",
        "diameter_mm": 5.0,
        "length_mm": 40,
        "packaging_qty": 200,  # hors identité
        "finish": "zinc",
    }
    assert DIMENSIONAL_FASTENER.identity_view(attrs) == {
        "type": "test",
        "diameter_mm": 5.0,
        "length_mm": 40,
    }

    m = generic_matcher.best_match(
        rule,
        attrs,
        [
            (
                99,
                "PMC-TEST-FAST-5X40",
                {"type": "test", "diameter_mm": 5.0, "length_mm": 40},
                "Test fastener",
            )
        ],
    )
    assert m.status == PROPOSAL_EXACT
    assert m.product_code == "PMC-TEST-FAST-5X40"
    assert m.algorithm_version == "test_fastener.v0"
