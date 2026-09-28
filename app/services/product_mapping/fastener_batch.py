"""Seed + apply batch FASTENER (VIS_BOIS / VIS_MULTI / CHEVILLE_METAL).

READ-mostly : seules écritures = Product PMC manquants + apply_exact_for_rule.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    CATEGORY_CHEVILLE_METAL,
    CATEGORY_VIS_BOIS,
    CATEGORY_VIS_MULTI,
)
from app.services.product_mapping import pipeline
from app.services.product_mapping.generic_matcher import REASON_EXACT
from app.services.product_mapping.rules import get_rule


def _code_from_pair(prefix: str, diameter_mm: float, length_mm: int) -> str:
    """PMC-VIS-BOIS-35X25 pour 3,5 × 25 (diamètre en dixièmes si non entier)."""
    d = Decimal(str(diameter_mm))
    if d == d.to_integral_value():
        d_part = str(int(d))
    else:
        d_part = str(int(d * 10))
    return f"{prefix}{d_part}X{int(length_mm)}"


def _name_for(type_label: str, diameter_mm: float, length_mm: int) -> str:
    d = Decimal(str(diameter_mm))
    d_txt = str(int(d)) if d == d.to_integral_value() else str(d).replace(".", ",")
    return f"{type_label} {d_txt} × {int(length_mm)} mm"


_FAMILY_META = {
    CATEGORY_VIS_BOIS: {
        "prefix": "PMC-VIS-BOIS-",
        "subcategory": "Vis bois",
        "type": "bois",
        "label": "Vis bois",
    },
    CATEGORY_VIS_MULTI: {
        "prefix": "PMC-VIS-MULTI-",
        "subcategory": "Vis multi",
        "type": "multi",
        "label": "Vis multi-matériaux",
    },
    CATEGORY_CHEVILLE_METAL: {
        "prefix": "PMC-CHEVILLE-METAL-",
        "subcategory": "Chevilles métal",
        "type": "cheville_metal",
        "label": "Cheville métal",
    },
}


def _existing_identity_codes(session: Session, type_value: str) -> set[tuple[float, int]]:
    """Identités (Ø, L) déjà présentes pour ce type (toutes sous-catégories fixation)."""
    found: set[tuple[float, int]] = set()
    rows = session.scalars(
        select(Product).where(
            Product.is_active.is_(True),
            Product.category == "Fixation",
        )
    ).all()
    for p in rows:
        attrs = p.attributes or {}
        if attrs.get("type") != type_value:
            continue
        d = attrs.get("diameter_mm")
        L = attrs.get("length_mm")
        if d is None or L is None:
            continue
        found.add((float(d), int(L)))
    return found


def collect_safe_identities(
    session: Session, category_code: str
) -> list[tuple[float, int]]:
    """Identités Ø×L des SP classifiés avec données complètes."""
    rule = get_rule(category_code)
    pairs: set[tuple[float, int]] = set()
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    for item in run.items:
        if not item.classified:
            continue
        if item.reason == "NOT_THIS_CATEGORY":
            continue
        d = item.attrs.get("diameter_mm")
        L = item.attrs.get("length_mm")
        if d is None or L is None:
            continue
        # Identité incomplète / faux positifs déjà exclus par le classifieur.
        pairs.add((float(d), int(L)))
    return sorted(pairs)


def seed_fastener_family_products(session: Session, category_code: str) -> dict[str, Any]:
    """Crée les Product PMC manquants pour les identités sûres de la famille."""
    meta = _FAMILY_META[category_code]
    type_value = meta["type"]
    existing = _existing_identity_codes(session, type_value)
    identities = collect_safe_identities(session, category_code)
    created = 0
    skipped_existing = 0
    created_codes: list[str] = []
    for d, L in identities:
        if (d, L) in existing:
            skipped_existing += 1
            continue
        code = _code_from_pair(meta["prefix"], d, L)
        if session.scalar(select(Product).where(Product.code == code)):
            skipped_existing += 1
            continue
        session.add(
            Product(
                code=code,
                name=_name_for(meta["label"], d, L),
                category="Fixation",
                subcategory=meta["subcategory"],
                reference_unit="pièce",
                description=(
                    f"{meta['label']} {d}×{L} — identité diamètre × longueur "
                    f"(type {type_value})."
                ),
                attributes={
                    "type": type_value,
                    "diameter_mm": d,
                    "length_mm": L,
                },
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        created_codes.append(code)
        existing.add((d, L))
    if created:
        session.flush()
    return {
        "category": category_code,
        "identities_found": len(identities),
        "created": created,
        "skipped_existing": skipped_existing,
        "created_codes": created_codes,
    }


def audit_exact_matches(session: Session, category_code: str) -> dict[str, Any]:
    """Vérifie Ø×L extrait == Product PMC pour toutes les propositions EXACT."""
    rule = get_rule(category_code)
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    mismatches: list[dict[str, Any]] = []
    exact_ok = 0
    for item in run.items:
        if item.match is None or item.match.reason != REASON_EXACT:
            continue
        d = item.attrs.get("diameter_mm")
        L = item.attrs.get("length_mm")
        product = session.get(Product, item.match.product_id)
        if product is None:
            mismatches.append(
                {
                    "sp_id": item.sp.id,
                    "reason": "product_missing",
                    "code": item.match.product_code,
                }
            )
            continue
        pa = product.attributes or {}
        pd, pL = pa.get("diameter_mm"), pa.get("length_mm")
        if float(pd) != float(d) or int(pL) != int(L):
            mismatches.append(
                {
                    "sp_id": item.sp.id,
                    "designation": (item.sp.designation or "")[:120],
                    "extracted": {"diameter_mm": d, "length_mm": L},
                    "product": {"code": product.code, "diameter_mm": pd, "length_mm": pL},
                }
            )
        else:
            exact_ok += 1
    return {
        "category": category_code,
        "exact_ok": exact_ok,
        "mismatches": mismatches,
        "mismatch_count": len(mismatches),
    }


def dry_run_summary(session: Session, category_code: str) -> dict[str, Any]:
    rule = get_rule(category_code)
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    return {
        "category": category_code,
        "candidates": run.initial_candidates,
        "not_this_category": run.not_this_category,
        "already_mapped": run.already_mapped,
        "exact": run.exact,
        "no_pmc_product": run.no_pmc_product,
        "insufficient_data": run.insufficient_data,
        "review": run.review,
        "ambiguous": run.ambiguous,
        "classified": run.classified,
    }


def apply_if_clean(session: Session, category_code: str) -> dict[str, Any]:
    audit = audit_exact_matches(session, category_code)
    if audit["mismatch_count"]:
        return {
            "category": category_code,
            "applied": 0,
            "skipped_reason": "exact_mismatch",
            "audit": audit,
        }
    rule = get_rule(category_code)
    result = pipeline.apply_exact_for_rule(session, rule, dry_run=False)
    return {
        "category": category_code,
        "applied": result.applied,
        "exact_candidates": result.exact_candidates,
        "already_mapped": result.already_mapped,
        "skipped": result.skipped,
        "audit": audit,
    }


def count_mapped_sp(session: Session) -> int:
    from sqlalchemy import func

    return int(
        session.scalar(
            select(func.count()).select_from(SupplierProduct).where(
                SupplierProduct.product_id.is_not(None)
            )
        )
        or 0
    )
