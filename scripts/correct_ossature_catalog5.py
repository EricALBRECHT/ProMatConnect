"""Correction ciblée et idempotente : unités pièce catalogue réel id=5.

Couvre ossature (rails/montants/fourrures) et plaques BA13.
Ne touche ni aux Product legacy, ni aux autres références du catalogue,
ni aux chantiers. Relançable sans effet de bord une fois les lignes correctes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, Supplier, SupplierProduct

logger = logging.getLogger(__name__)

# Unité canonique Product (accent) — ne pas inventer "piece" / "m2" en parallèle.
CANONICAL_PIECE = "pièce"
TARGET_CATALOG_ID = 5

# (supplier_name, supplier_reference) → (product_code, supplier_unit, pack_qty, ref_qty)
CATALOG5_PIECE_FIXES: dict[tuple[str, str], tuple[str, str, Decimal, Decimal]] = {
    # --- Ossature ---
    ("LEROY_MERLIN", "85343284"): ("PMC-RAIL-R48-3000", "lot", Decimal("10"), Decimal("10")),
    ("LEROY_MERLIN", "85343291"): ("PMC-RAIL-R48-3000", "lot", Decimal("10"), Decimal("10")),
    ("BRICO_DEPOT", "3760424120001"): ("PMC-RAIL-R48-3000", "lot", Decimal("10"), Decimal("10")),
    ("LEROY_MERLIN", "67532150"): ("PMC-RAIL-R70-3000", CANONICAL_PIECE, Decimal("1"), Decimal("1")),
    ("LEROY_MERLIN", "85343285"): ("PMC-MONTANT-M48-2500", "lot", Decimal("10"), Decimal("10")),
    ("BRICO_DEPOT", "3760424120018"): (
        "PMC-MONTANT-M48-2500",
        "lot",
        Decimal("10"),
        Decimal("10"),
    ),
    ("LEROY_MERLIN", "67532115"): (
        "PMC-MONTANT-M48-3000",
        CANONICAL_PIECE,
        Decimal("1"),
        Decimal("1"),
    ),
    ("LEROY_MERLIN", "80134520"): (
        "PMC-MONTANT-M48-3000",
        CANONICAL_PIECE,
        Decimal("1"),
        Decimal("1"),
    ),
    ("BRICO_DEPOT", "3334160299712"): (
        "PMC-MONTANT-M48-2500",
        CANONICAL_PIECE,
        Decimal("1"),
        Decimal("1"),
    ),
    ("BRICO_DEPOT", "3760424120025"): (
        "PMC-FOURRURE-F45-3000",
        "lot",
        Decimal("10"),
        Decimal("10"),
    ),
    # --- Plaques (surface technique sur Product, pas en reference_quantity) ---
    ("LEROY_MERLIN", "70505960"): (
        "PMC-BA13-STD-2500X1200",
        "plaque",
        Decimal("1"),
        Decimal("1"),
    ),
    ("LEROY_MERLIN", "68587554"): (
        "PMC-BA13-STD-2500X1200",
        "plaque",
        Decimal("1"),
        Decimal("1"),
    ),
    ("LEROY_MERLIN", "65384060"): (
        "PMC-BA13-STD-2500X600",
        "plaque",
        Decimal("1"),
        Decimal("1"),
    ),
    ("BRICO_DEPOT", "3334160144715"): (
        "PMC-BA13-STD-2500X1200",
        "plaque",
        Decimal("1"),
        Decimal("1"),
    ),
    ("BRICO_DEPOT", "3334160158651"): (
        "PMC-BA13-HYDRO-2500X1200",
        "plaque",
        Decimal("1"),
        Decimal("1"),
    ),
    ("BRICO_DEPOT", "3334160500023"): (
        "PMC-BA13-MULTI-2500X1200",
        "plaque",
        Decimal("1"),
        Decimal("1"),
    ),
    ("BRICO_DEPOT", "3334160524579"): (
        "PMC-BA13-LIGHT-2500X1200",
        "plaque",
        Decimal("1"),
        Decimal("1"),
    ),
}

# Alias historique
OSSATURE_FIXES = {
    k: v for k, v in CATALOG5_PIECE_FIXES.items() if v[0].startswith(("PMC-RAIL-", "PMC-MONTANT-", "PMC-FOURRURE-"))
}


@dataclass
class CorrectionReport:
    found: list[str] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)
    already_correct: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "found": list(self.found),
            "corrected": list(self.corrected),
            "already_correct": list(self.already_correct),
            "anomalies": list(self.anomalies),
            "counts": {
                "found": len(self.found),
                "corrected": len(self.corrected),
                "already_correct": len(self.already_correct),
                "anomalies": len(self.anomalies),
            },
        }


def _key_label(supplier: str, reference: str) -> str:
    return f"{supplier}:{reference}"


def _eq(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        if a is None or b is None:
            return a is b
        return Decimal(str(a)) == Decimal(str(b))
    return a == b


def correct_catalog5_piece_units(
    session: Session, *, catalog_id: int = TARGET_CATALOG_ID
) -> CorrectionReport:
    """Corrige les SupplierProduct pièce (ossature + plaques) du catalogue réel."""
    report = CorrectionReport()
    needed_codes = {code for code, *_ in CATALOG5_PIECE_FIXES.values()}
    product_by_code = {
        p.code: p for p in session.scalars(select(Product).where(Product.code.in_(needed_codes)))
    }

    for (supplier_name, reference), (
        product_code,
        supplier_unit,
        pack_qty,
        ref_qty,
    ) in CATALOG5_PIECE_FIXES.items():
        label = _key_label(supplier_name, reference)
        product = product_by_code.get(product_code)
        if product is None:
            report.anomalies.append(f"{label}: Product {product_code} introuvable")
            continue
        if product.reference_unit != CANONICAL_PIECE:
            report.anomalies.append(
                f"{label}: Product {product_code} a reference_unit="
                f"{product.reference_unit!r} (attendu {CANONICAL_PIECE!r})"
            )
            continue

        supplier = session.scalar(select(Supplier).where(Supplier.name == supplier_name))
        if supplier is None:
            report.anomalies.append(f"{label}: fournisseur introuvable")
            continue

        sp = session.scalar(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier.id,
                SupplierProduct.supplier_reference == reference,
                SupplierProduct.introduced_by_catalog_id == catalog_id,
            )
        )
        if sp is None:
            report.anomalies.append(
                f"{label}: SupplierProduct absent (catalog_id={catalog_id})"
            )
            continue

        report.found.append(label)
        desired = {
            "product_id": product.id,
            "supplier_unit": supplier_unit,
            "packaging_quantity": pack_qty,
            "reference_unit": CANONICAL_PIECE,
            "reference_quantity": ref_qty,
        }
        current = {
            "product_id": sp.product_id,
            "supplier_unit": sp.supplier_unit,
            "packaging_quantity": sp.packaging_quantity,
            "reference_unit": sp.reference_unit,
            "reference_quantity": sp.reference_quantity,
        }
        if all(_eq(current[k], desired[k]) for k in desired):
            if sp.correction_source != "manual":
                sp.correction_source = "manual"
                report.corrected.append(f"{label} (flag override)")
            else:
                report.already_correct.append(label)
            continue

        sp.product_id = desired["product_id"]
        sp.supplier_unit = desired["supplier_unit"]
        sp.packaging_quantity = desired["packaging_quantity"]
        sp.reference_unit = desired["reference_unit"]
        sp.reference_quantity = desired["reference_quantity"]
        sp.correction_source = "manual"
        report.corrected.append(label)
        logger.info(
            "Catalog %s corrigé : %s → %s (%s×%s)",
            catalog_id,
            label,
            product_code,
            pack_qty,
            CANONICAL_PIECE,
        )

    session.flush()
    return report


# Alias API précédente
correct_ossature_catalog5 = correct_catalog5_piece_units


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from app.config import Settings
    from app.database import make_engine
    from scripts.normalized_catalog import seed_normalized_catalog

    settings = Settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        seed_normalized_catalog(session)
        report = correct_catalog5_piece_units(session)
        session.commit()
        print(report.to_dict())


if __name__ == "__main__":
    main()
