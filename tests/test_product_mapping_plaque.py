"""Tests V1.1 mapping PLAQUE_PLATRE — classification, raisons, UNKNOWN, mappings manuels."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    PROPOSAL_EXACT,
    PROPOSAL_REVIEW,
    PROPOSAL_UNMAPPED,
    ProductCategory,
)
from app.services.bricodepot_catalog_import import resolve_brico_supplier
from app.services.product_mapping.plaque_extractor import (
    REASON_NOT_THIS_CATEGORY,
    extract_dimensions_mm,
    extract_plaque_platre,
    extract_plaque_type_and_flags,
    is_accessory_not_plaque,
    looks_like_plaque_platre,
)
from app.services.product_mapping.plaque_matcher import best_match, score_against_product
from app.services.product_mapping.service import (
    ProductMappingService,
    ensure_plaque_platre_category,
)


def test_extract_dimensions_mm_variants():
    assert extract_dimensions_mm("Plaque 2500 x 1200 x 13") == (2500, 1200, 13)
    assert extract_dimensions_mm("2500×1200×13mm") == (2500, 1200, 13)
    assert extract_dimensions_mm("L.2,50 x l.1,20m x Ep.13mm") == (2500, 1200, 13)
    assert extract_dimensions_mm("2,50 x 1,20 m x 13 mm") == (2500, 1200, 13)
    bad = extract_dimensions_mm("pas de dimensions ici")
    assert bad == (None, None, None)


def test_vraie_plaque_classee_correctement():
    r = extract_plaque_platre(
        designation="Plaque de plâtre BA13 standard 2500 x 1200 x 13 mm"
    )
    assert r.classified is True
    assert r.category_code == "PLAQUE_PLATRE"
    assert r.reason is None
    assert looks_like_plaque_platre(
        "Plaque de plâtre BA13 standard 2500 x 1200 x 13 mm"
    )


def test_vis_plaque_not_this_category():
    designation = "Seau de 1000 vis plaque de plâtre"
    assert is_accessory_not_plaque(designation) is True
    r = extract_plaque_platre(designation=designation)
    assert r.classified is False
    assert r.reason == REASON_NOT_THIS_CATEGORY


def test_enduit_plaque_not_this_category():
    r = extract_plaque_platre(designation="Enduit plaque de plâtre prêt à l'emploi 25kg")
    assert r.classified is False
    assert r.reason == REASON_NOT_THIS_CATEGORY


def test_accessoire_bande_not_this_category():
    r = extract_plaque_platre(designation="Bande à joint pour plaque de plâtre 90m")
    assert r.classified is False
    assert r.reason == REASON_NOT_THIS_CATEGORY


def test_ba18_reconnu_comme_plaque():
    r = extract_plaque_platre(
        designation="Plaque de plâtre BA18 standard 2500 x 1200 x 18 mm"
    )
    assert r.classified is True
    assert r.attributes["type"] == "standard"
    assert r.attributes["thickness_mm"] == 18
    assert r.attributes["length_mm"] == 2500
    assert r.attributes["width_mm"] == 1200
    assert r.attributes["hydrofuge"] is None  # UNKNOWN ≠ false


def test_resistante_au_feu_not_standard():
    r = extract_plaque_platre(
        designation=(
            "Plaque de plâtre BA 13 résistante au feu NF- "
            "L. 2,50 x l. 1,20 m x Ép. 13 mm"
        )
    )
    assert r.classified is True
    assert r.attributes["type"] == "feu"
    assert r.attributes["fire_resistant"] is True
    # Ne doit pas matcher EXACT un Product standard
    m = best_match(
        r.attributes,
        [
            (
                1,
                "PMC-BA13-STD-2500X1200",
                {
                    "length_mm": 2500,
                    "width_mm": 1200,
                    "thickness_mm": 13,
                    "type": "standard",
                },
            )
        ],
    )
    assert m.status != PROPOSAL_EXACT
    assert m.reason == "NO_PMC_PRODUCT"


def test_ba_thickness_inferred_when_ep_missing():
    r = extract_plaque_platre(
        designation="Demi-plaque de plâtre BA 13 hydrofuge NF - L. 2,50 x l. 0,60 m"
    )
    assert r.classified is True
    assert r.attributes["thickness_mm"] == 13
    assert r.attributes["length_mm"] == 2500
    assert r.attributes["width_mm"] == 600
    assert r.attributes["type"] == "hydrofuge"


def test_ba18_sans_pmc_no_pmc_product():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 18,
        "type": "standard",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    # Candidats BA13 uniquement — aucun BA18
    candidates = [
        (
            1,
            "PMC-BA13-STD-2500X1200",
            {
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "standard",
            },
        ),
    ]
    m = best_match(extracted, candidates)
    assert m.status == PROPOSAL_UNMAPPED
    assert m.reason == "NO_PMC_PRODUCT"
    assert m.product_id is None


def test_dimensions_insuffisantes_insufficient_data():
    extracted = {
        "length_mm": None,
        "width_mm": None,
        "thickness_mm": 13,
        "type": "standard",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    m = best_match(
        extracted,
        [
            (
                1,
                "PMC-BA13-STD-2500X1200",
                {
                    "length_mm": 2500,
                    "width_mm": 1200,
                    "thickness_mm": 13,
                    "type": "standard",
                },
            )
        ],
    )
    assert m.status == PROPOSAL_UNMAPPED
    assert m.reason == "INSUFFICIENT_DATA"


def test_plusieurs_candidats_ambiguous():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "standard",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    candidates = [
        (
            1,
            "PMC-BA13-STD-A",
            {
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "standard",
            },
        ),
        (
            2,
            "PMC-BA13-STD-B",
            {
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "standard",
            },
        ),
    ]
    m = best_match(extracted, candidates)
    assert m.reason == "AMBIGUOUS"
    assert m.status == PROPOSAL_REVIEW


def test_unknown_not_false():
    std = extract_plaque_type_and_flags("Plaque BA13 standard 2500x1200x13")
    assert std["type"] == "standard"
    assert std["hydrofuge"] is None
    assert std["fire_resistant"] is None
    assert std["acoustic"] is None

    r = extract_plaque_platre(designation="Plaque BA13 2500 x 1200 x 13 mm")
    assert r.classified is True
    assert r.attributes["hydrofuge"] is None
    assert r.attributes["fire_resistant"] is None
    assert r.attributes["acoustic"] is None

    # Pas d'EXACT artificiel si type manquant côté extrait
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": None,
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    m = score_against_product(
        extracted,
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "standard",
        },
        product_id=1,
        product_code="PMC-STD",
    )
    assert m.status != PROPOSAL_EXACT
    assert m.status == PROPOSAL_REVIEW


def test_exact_match_same_dims_type():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "standard",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    m = score_against_product(
        extracted,
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "standard",
            "surface_m2": 3.0,
        },
        product_id=1,
        product_code="PMC-BA13-STD-2500X1200",
    )
    assert m.status == PROPOSAL_EXACT
    assert m.reason == "EXACT"
    assert m.score == 1.0


def test_hydro_incompatible_not_exact():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "hydrofuge",
        "hydrofuge": True,
        "fire_resistant": None,
        "acoustic": None,
    }
    m = score_against_product(
        extracted,
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "standard",
        },
        product_id=1,
        product_code="PMC-STD",
    )
    assert m.status != PROPOSAL_EXACT


def test_best_match_picks_exact_among_candidates():
    extracted = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "legere",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    candidates = [
        (
            1,
            "PMC-BA13-STD-2500X1200",
            {
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "standard",
            },
        ),
        (
            2,
            "PMC-BA13-LIGHT-2500X1200",
            {
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "legere",
            },
        ),
    ]
    m = best_match(extracted, candidates)
    assert m.status == PROPOSAL_EXACT
    assert m.product_code == "PMC-BA13-LIGHT-2500X1200"


def test_mapping_manuel_preserve(session):
    ensure_plaque_platre_category(session)
    product = session.scalar(
        select(Product).where(Product.code == "PMC-BA13-LIGHT-2500X1200")
    )
    if product is None:
        product = Product(
            code="PMC-BA13-LIGHT-2500X1200",
            name="Plaque BA13 Purelight",
            category="Plâtrerie",
            subcategory="Plaques de plâtre",
            reference_unit="pièce",
            attributes={
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "legere",
            },
        )
        session.add(product)
        session.flush()

    supplier = resolve_brico_supplier(session)
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=supplier.id,
        supplier_reference="9990000000001",
        designation="Plaque de plâtre BA13 Purelight 2500 x 1200 x 13 mm",
        supplier_unit="La pièce",
        reference_quantity=Decimal("1"),
        packaging_quantity=Decimal("1"),
        correction_source="manual",
    )
    session.add(sp)
    session.flush()
    old_pid = sp.product_id

    svc = ProductMappingService(session)
    result = svc.run_plaque_platre(
        supplier_name=supplier.name,
        limit=10,
        dry_run=True,
        persist=False,
        example_limit=5,
    )
    session.refresh(sp)
    assert sp.product_id == old_pid
    assert sp.correction_source == "manual"
    assert result.already_mapped >= 1
    assert any(e.reason == "ALREADY_MAPPED" for e in result.examples)


def test_proposal_never_changes_product_id(session):
    ensure_plaque_platre_category(session)
    product = session.scalar(
        select(Product).where(Product.code == "PMC-BA13-LIGHT-2500X1200")
    )
    if product is None:
        product = Product(
            code="PMC-BA13-LIGHT-2500X1200",
            name="Plaque BA13 Purelight",
            category="Plâtrerie",
            subcategory="Plaques de plâtre",
            reference_unit="pièce",
            attributes={
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "type": "legere",
            },
        )
        session.add(product)
        session.flush()

    supplier = resolve_brico_supplier(session)
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=supplier.id,
        supplier_reference="9990000000002",
        designation="Plaque de plâtre BA13 Purelight 2500 x 1200 x 13 mm",
        supplier_unit="La pièce",
        reference_quantity=Decimal("1"),
        packaging_quantity=Decimal("1"),
        correction_source="manual",
    )
    session.add(sp)
    session.flush()
    old_pid = sp.product_id

    svc = ProductMappingService(session)
    result = svc.run_plaque_platre(
        supplier_name=supplier.name,
        limit=10,
        dry_run=False,
        persist=True,
        example_limit=3,
    )
    session.flush()
    session.refresh(sp)
    assert sp.product_id == old_pid
    assert sp.correction_source == "manual"
    assert result.classified >= 1
    cat = session.scalar(
        select(ProductCategory).where(ProductCategory.code == "PLAQUE_PLATRE")
    )
    assert cat is not None


def test_ensure_category_idempotent(session):
    a = ensure_plaque_platre_category(session)
    b = ensure_plaque_platre_category(session)
    assert a.id == b.id


# --- V1.2 : 6 variantes PMC manquantes → EXACT ---

_V12_VARIANTS = [
    (
        "PMC-BA13-HYDRO-2500X600",
        {
            "length_mm": 2500,
            "width_mm": 600,
            "thickness_mm": 13,
            "type": "hydrofuge",
            "hydrofuge": True,
            "fire_resistant": None,
            "acoustic": None,
        },
    ),
    (
        "PMC-BA13-PHONI-2500X1200",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "phonique",
            "hydrofuge": None,
            "fire_resistant": None,
            "acoustic": True,
        },
    ),
    (
        "PMC-BA10-STD-2500X1200",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 10,
            "type": "standard",
            "hydrofuge": None,
            "fire_resistant": None,
            "acoustic": None,
        },
    ),
    (
        "PMC-BA13-HYDRO-1250X600",
        {
            "length_mm": 1250,
            "width_mm": 600,
            "thickness_mm": 13,
            "type": "hydrofuge",
            "hydrofuge": True,
            "fire_resistant": None,
            "acoustic": None,
        },
    ),
    (
        "PMC-BA13-FEU-2500X1200",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "feu",
            "hydrofuge": None,
            "fire_resistant": True,
            "acoustic": None,
        },
    ),
    (
        "PMC-BA13-STD-1250X600",
        {
            "length_mm": 1250,
            "width_mm": 600,
            "thickness_mm": 13,
            "type": "standard",
            "hydrofuge": None,
            "fire_resistant": None,
            "acoustic": None,
        },
    ),
]


def test_v12_variants_exact_against_seeded_pmc(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.flush()
    for code, extracted in _V12_VARIANTS:
        product = session.scalar(select(Product).where(Product.code == code))
        assert product is not None, f"missing {code}"
        m = best_match(
            extracted,
            [(product.id, product.code, product.attributes)],
        )
        assert m.status == PROPOSAL_EXACT, f"{code}: {m.status} {m.reason}"
        assert m.product_code == code
        assert m.reason == "EXACT"


def test_v12_no_collision_with_ba13_standard(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.flush()
    std = session.scalar(
        select(Product).where(Product.code == "PMC-BA13-STD-2500X1200")
    )
    assert std is not None
    # BA10 10 mm ≠ BA13 13 mm
    ba10 = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 10,
        "type": "standard",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    m = score_against_product(
        ba10, std.attributes, product_id=std.id, product_code=std.code
    )
    assert m.status != PROPOSAL_EXACT
    # feu ≠ standard
    feu = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "feu",
        "hydrofuge": None,
        "fire_resistant": True,
        "acoustic": None,
    }
    m2 = score_against_product(
        feu, std.attributes, product_id=std.id, product_code=std.code
    )
    assert m2.status != PROPOSAL_EXACT
    assert m2.reason == "NO_PMC_PRODUCT"
    # phonique ≠ standard
    phon = {
        "length_mm": 2500,
        "width_mm": 1200,
        "thickness_mm": 13,
        "type": "phonique",
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": True,
    }
    m3 = score_against_product(
        phon, std.attributes, product_id=std.id, product_code=std.code
    )
    assert m3.status != PROPOSAL_EXACT


def test_v12_seed_idempotent_no_plaque_duplicates(session):
    from scripts.normalized_catalog import (
        NORMALIZED_PRODUCT_COUNT,
        seed_normalized_catalog,
    )

    n1 = seed_normalized_catalog(session)
    session.flush()
    codes = [c for c, _ in _V12_VARIANTS]
    first = list(session.scalars(select(Product).where(Product.code.in_(codes))).all())
    assert len(first) == 6
    n2 = seed_normalized_catalog(session)
    session.flush()
    second = list(session.scalars(select(Product).where(Product.code.in_(codes))).all())
    assert len(second) == 6
    assert {p.id for p in first} == {p.id for p in second}
    assert n2 == 0
    # Pas de doublon technique (même L/l/Ep/type)
    plaques = list(
        session.scalars(
            select(Product).where(Product.subcategory == "Plaques de plâtre")
        ).all()
    )
    identities = []
    for p in plaques:
        attrs = p.attributes or {}
        key = (
            attrs.get("length_mm"),
            attrs.get("width_mm"),
            attrs.get("thickness_mm"),
            attrs.get("type"),
        )
        if None not in key:
            identities.append(key)
    assert len(identities) == len(set(identities))
    assert NORMALIZED_PRODUCT_COUNT >= 26  # 20 anciens + 6 V1.2 au moins


# --- V1.3 : syntaxes Brico réelles (groupe A) + exclusions faux positifs ---

def test_v13_unlabeled_dims_hydro_250_x_120():
    # Ex. réel : « Plaque de plâtre BA 13 hydrofuge NF - 2,50 x 1,20 m »
    r = extract_plaque_platre(
        designation="Plaque de plâtre BA 13 hydrofuge NF - 2,50 x 1,20 m"
    )
    assert r.classified is True
    assert r.attributes["type"] == "hydrofuge"
    assert r.attributes["length_mm"] == 2500
    assert r.attributes["width_mm"] == 1200
    assert r.attributes["thickness_mm"] == 13
    assert r.attributes["hydrofuge"] is True


def test_v13_unlabeled_dims_standard_2_5_x_1_2m():
    # Ex. réel : « Plaque BA13 NF standard - 2,5 X 1,2M »
    r = extract_plaque_platre(designation="Plaque BA13 NF standard - 2,5 X 1,2M")
    assert r.classified is True
    assert r.attributes["type"] == "standard"
    assert r.attributes["length_mm"] == 2500
    assert r.attributes["width_mm"] == 1200
    assert r.attributes["thickness_mm"] == 13


def test_v13_cm_pair_and_hydro_shorthand():
    # Ex. réel : « Plaque de plâtre BA13 hydro 120x250 cm ép. 13 mm pour pièces humides »
    r = extract_plaque_platre(
        designation=(
            "Plaque de plâtre BA13 hydro 120x250 cm ép. 13 mm pour pièces humides"
        )
    )
    assert r.classified is True
    assert r.attributes["type"] == "hydrofuge"
    assert r.attributes["length_mm"] == 2500
    assert r.attributes["width_mm"] == 1200
    assert r.attributes["thickness_mm"] == 13


def test_v13_epaisseur_cm_125():
    # Ex. réel : « … 250 cm x 120 cm épaisseur 1,25 cm bords amincis »
    r = extract_plaque_platre(
        designation=(
            "Plaque de plâtre BA13, 250 cm x 120 cm épaisseur 1,25 cm bords amincis"
        )
    )
    assert r.classified is True
    assert r.attributes["length_mm"] == 2500
    assert r.attributes["width_mm"] == 1200
    assert r.attributes["thickness_mm"] == 13  # 1,25 cm → 12,5 mm → 13
    assert r.attributes["type"] == "standard"


def test_v13_bords_amincis_3000_extracted():
    # Info présente ; pas de Product PMC 3000×1200 — extraction OK
    r = extract_plaque_platre(
        designation="Plaque de plâtre BA13 à bords amincis - 3,00 m x 1,20 m"
    )
    assert r.classified is True
    assert r.attributes["length_mm"] == 3000
    assert r.attributes["width_mm"] == 1200
    assert r.attributes["thickness_mm"] == 13
    assert r.attributes["type"] == "standard"


def test_v13_doublage_not_plaque():
    r = extract_plaque_platre(
        designation=(
            "Doublage plaque de plâtre + polystyrène TH 35 - "
            "L. 2,50 x l. 1,20 m x Ép. 10 + 40 mm"
        )
    )
    assert r.classified is False
    assert r.reason == REASON_NOT_THIS_CATEGORY


def test_v13_crochet_souscouche_ossature_not_plaque():
    assert extract_plaque_platre(
        designation="2 crochets griffe plaque de plâtre - charge maxi : 7 kg"
    ).classified is False
    assert extract_plaque_platre(
        designation="Sous-couche plaque de plâtre blanc 5 L"
    ).classified is False
    assert extract_plaque_platre(
        designation=(
            "Lot 10 montants m48 250 cm en acier galvanisé "
            "pour cloison plaques de plâtre"
        )
    ).classified is False
    assert extract_plaque_platre(
        designation=(
            "Boîte à encastrer Eco Batibox profondeur 40 mm "
            "pour plaque de plâtre - 2 postes"
        )
    ).classified is False


def test_v13_group_a_exact_against_pmc(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.flush()
    candidates = [
        (p.id, p.code, p.attributes)
        for p in session.scalars(
            select(Product).where(Product.subcategory == "Plaques de plâtre")
        ).all()
    ]
    cases = [
        (
            "Plaque de plâtre BA 13 hydrofuge NF - 2,50 x 1,20 m",
            "PMC-BA13-HYDRO-2500X1200",
        ),
        ("Plaque BA13 NF standard - 2,5 X 1,2M", "PMC-BA13-STD-2500X1200"),
        (
            "Plaque de plâtre BA13 hydro 120x250 cm ép. 13 mm pour pièces humides",
            "PMC-BA13-HYDRO-2500X1200",
        ),
        (
            "Plaque de plâtre BA13, 250 cm x 120 cm épaisseur 1,25 cm bords amincis",
            "PMC-BA13-STD-2500X1200",
        ),
    ]
    for designation, expected_code in cases:
        r = extract_plaque_platre(designation=designation)
        m = best_match(r.attributes, candidates)
        assert m.status == PROPOSAL_EXACT, f"{designation}: {m.status} {m.reason}"
        assert m.product_code == expected_code


def test_v14_ba13_3000x1200_exact(session):
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.flush()
    product = session.scalar(
        select(Product).where(Product.code == "PMC-BA13-STD-3000X1200")
    )
    assert product is not None
    assert product.attributes["surface_m2"] == 3.6
    r = extract_plaque_platre(
        designation="Plaque de plâtre BA13 à bords amincis - 3,00 m x 1,20 m"
    )
    assert r.classified is True
    assert r.attributes["length_mm"] == 3000
    assert r.attributes["width_mm"] == 1200
    assert r.attributes["thickness_mm"] == 13
    assert r.attributes["type"] == "standard"
    assert r.attributes["hydrofuge"] is None
    m = best_match(
        r.attributes,
        [(product.id, product.code, product.attributes)],
    )
    assert m.status == PROPOSAL_EXACT
    assert m.product_code == "PMC-BA13-STD-3000X1200"
