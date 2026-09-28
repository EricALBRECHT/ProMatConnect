"""Tests lot CORNIERE_PVC / ROND / TUBE / FER_BETON / PANNEAU_MDF."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    CATEGORY_CORNIERE_PVC,
    CATEGORY_FER_BETON,
    CATEGORY_PANNEAU_MDF,
    CATEGORY_ROND_ACIER,
    CATEGORY_TUBE_ROND_ACIER,
)
from app.services.bricodepot_catalog_import import resolve_brico_supplier
from app.services.product_mapping.corniere_pvc_extractor import (
    extract_corniere_pvc,
    is_corniere_pvc,
)
from app.services.product_mapping.fer_beton_extractor import (
    extract_fer_beton,
    is_fer_beton,
)
from app.services.product_mapping.panneau_mdf_extractor import (
    extract_panneau_mdf,
    is_panneau_mdf,
)
from app.services.product_mapping.profiles_batch import (
    apply_if_clean,
    audit_exact_matches,
    dry_run_summary,
    seed_family,
)
from app.services.product_mapping.rond_acier_extractor import (
    extract_rond_acier,
    is_rond_acier,
)
from app.services.product_mapping.rules import registered_codes
from app.services.product_mapping.tube_rond_acier_extractor import (
    extract_tube_rond_acier,
    is_tube_rond_acier,
)


def test_lot_categories_registered():
    codes = registered_codes()
    for code in (
        CATEGORY_CORNIERE_PVC,
        CATEGORY_ROND_ACIER,
        CATEGORY_TUBE_ROND_ACIER,
        CATEGORY_FER_BETON,
        CATEGORY_PANNEAU_MDF,
    ):
        assert code in codes


def test_corniere_true_and_fp():
    assert is_corniere_pvc("Cornière PVC blanc - 10 x 10 mm - 2,50 m")
    ext = extract_corniere_pvc("Cornière PVC blanc - 20 x 30 mm x 2,50 m")
    assert ext.classified
    assert ext.attributes["kind"] == "CORNIERE_PVC"
    assert ext.attributes["profile"] == "20X30_blanc"
    assert ext.attributes["nominal_length_mm"] == 2500

    assert not is_corniere_pvc(
        "Cornière PVC acier inoxidable - 20 x 20 mm x 2 m"
    )
    assert not is_corniere_pvc("Lot de 2 cornières PVC")
    assert not is_corniere_pvc("Cornière acier galvanisé 30 x 30")


def test_rond_tube_fer_separation():
    assert is_rond_acier("Rond acier étiré brut poli Ø.8mm 1 m")
    assert not is_rond_acier("Tube rond acier profilé AF - 20 x 1,5 mm 1 m")
    assert not is_rond_acier("Fer à béton torsadé - Ø 6 mm x L. 6 m")

    assert is_tube_rond_acier("Tube rond acier profilé AF - 20 x 1,5 mm 1 m")
    assert not is_tube_rond_acier("Rond acier étiré brut poli Ø.8mm 1 m")
    assert not is_tube_rond_acier("Connecteur PVC tube rond 20 mm")

    assert is_fer_beton("Fer à béton torsadé rond acier diamètre 10 mm L. 3 m")
    assert not is_fer_beton("Rond acier étiré brut poli Ø.8mm 1 m")

    r = extract_rond_acier("Rond acier étiré brut poli Ø.8mm 1 m")
    assert r.attributes["profile"] == "D8"
    assert r.attributes["nominal_length_mm"] == 1000

    t = extract_tube_rond_acier("Tube rond acier profilé AF - 20 x 1,5 mm 1 m")
    assert t.attributes["profile"] == "20X1,5"
    assert t.attributes["nominal_length_mm"] == 1000

    f = extract_fer_beton("Fer à béton torsadé - Ø 6 mm x L. 6 m")
    assert f.attributes["profile"] == "D6"
    assert f.attributes["nominal_length_mm"] == 6000


def test_panneau_mdf_and_fp():
    assert is_panneau_mdf("Panneau bois MDF 810 x 405 mm - Ép. 18 mm")
    ext = extract_panneau_mdf("Panneau bois MDF 1 220 x 610 mm - Ép. 6 mm")
    assert ext.classified
    assert ext.attributes == {
        "type": "mdf",
        "length_mm": 1220,
        "width_mm": 610,
        "thickness_mm": 6,
    }
    assert not is_panneau_mdf(
        "Lot de 4 équerres pour panneau bois - Ep : 1,8 mm"
    )
    assert not is_panneau_mdf("Panneau en bois naturel arc - 180 X 180. Ep. 28 mm")
    # Format hors liste autorisée → classifié mais identité incomplète
    big = extract_panneau_mdf(
        "Panneau de fibres de bois MDF L. 2,44 m x l. 1,22 m x Ép. 12 mm"
    )
    assert big.classified
    assert big.attributes["length_mm"] is None


def test_seed_apply_idempotent(session):
    supplier = resolve_brico_supplier(session)
    samples = [
        ("c1", "Cornière PVC blanc - 10 x 10 mm - 2,50 m"),
        ("r1", "Rond acier étiré brut poli Ø.8mm 1 m"),
        ("t1", "Tube rond acier profilé AF - 20 x 1,5 mm 1 m"),
        ("f1", "Fer à béton torsadé - Ø 6 mm x L. 6 m"),
        ("m1", "Panneau bois MDF 810 x 405 mm - Ép. 18 mm"),
        ("fp", "Lot de 4 équerres pour panneau bois - Ep : 1,8 mm"),
    ]
    for ref, des in samples:
        session.add(
            SupplierProduct(
                product_id=None,
                supplier_id=supplier.id,
                supplier_reference=ref,
                designation=des,
                supplier_unit="La pièce",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                correction_source="import",
            )
        )
    session.flush()

    for code in (
        CATEGORY_CORNIERE_PVC,
        CATEGORY_ROND_ACIER,
        CATEGORY_TUBE_ROND_ACIER,
        CATEGORY_FER_BETON,
        CATEGORY_PANNEAU_MDF,
    ):
        first = seed_family(session, code)
        second = seed_family(session, code)
        assert first["created"] >= 1
        assert second["created"] == 0
        audit = audit_exact_matches(session, code)
        assert audit["mismatch_count"] == 0
        applied = apply_if_clean(session, code)
        assert applied["applied"] >= 1
        again = apply_if_clean(session, code)
        assert again["applied"] == 0
        after = dry_run_summary(session, code)
        assert after["exact"] == 0
        assert after["already_mapped"] >= 1

    assert session.scalar(
        select(Product).where(Product.code == "PMC-PANNEAU-MDF-810X405X18")
    )
    assert session.scalar(
        select(Product).where(Product.code.like("PMC-CORNIERE-PVC-%"))
    )
