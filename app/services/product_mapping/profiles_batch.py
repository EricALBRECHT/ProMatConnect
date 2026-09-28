"""Seed + apply lot CORNIERE_PVC / ROND / TUBE / FER_BETON / PANNEAU_MDF."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, SupplierProduct
from app.models.product_mapping import (
    CATEGORY_CORNIERE_PVC,
    CATEGORY_FER_BETON,
    CATEGORY_PANNEAU_MDF,
    CATEGORY_ROND_ACIER,
    CATEGORY_TUBE_ROND_ACIER,
)
from app.services.product_mapping import pipeline
from app.services.product_mapping.generic_matcher import REASON_EXACT
from app.services.product_mapping.rules import get_rule

LOT_CATEGORIES = (
    CATEGORY_CORNIERE_PVC,
    CATEGORY_ROND_ACIER,
    CATEGORY_TUBE_ROND_ACIER,
    CATEGORY_FER_BETON,
    CATEGORY_PANNEAU_MDF,
)

_LINEAR_META = {
    CATEGORY_CORNIERE_PVC: {
        "prefix": "PMC-CORNIERE-PVC-",
        "category": "Profilé",
        "subcategory": "Cornière PVC",
        "label": "Cornière PVC",
    },
    CATEGORY_ROND_ACIER: {
        "prefix": "PMC-ROND-ACIER-",
        "category": "Profilé",
        "subcategory": "Rond acier",
        "label": "Rond acier",
    },
    CATEGORY_TUBE_ROND_ACIER: {
        "prefix": "PMC-TUBE-ROND-ACIER-",
        "category": "Profilé",
        "subcategory": "Tube rond acier",
        "label": "Tube rond acier",
    },
    CATEGORY_FER_BETON: {
        "prefix": "PMC-FER-BETON-",
        "category": "Profilé",
        "subcategory": "Fer à béton",
        "label": "Fer à béton",
    },
}


def _linear_code(prefix: str, profile: str, length_mm: int) -> str:
    safe = (
        str(profile)
        .upper()
        .replace(",", "P")
        .replace(".", "P")
        .replace(" ", "")
    )
    return f"{prefix}{safe}-{int(length_mm)}"


def _mdf_code(length_mm: int, width_mm: int, thickness_mm: int) -> str:
    return f"PMC-PANNEAU-MDF-{int(length_mm)}X{int(width_mm)}X{int(thickness_mm)}"


def seed_linear_family(session: Session, category_code: str) -> dict[str, Any]:
    meta = _LINEAR_META[category_code]
    rule = get_rule(category_code)
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    identities: set[tuple[str, str, int]] = set()
    for item in run.items:
        if not item.classified:
            continue
        kind = item.attrs.get("kind")
        profile = item.attrs.get("profile")
        nominal = item.attrs.get("nominal_length_mm")
        if kind is None or profile is None or nominal is None:
            continue
        identities.add((str(kind), str(profile), int(nominal)))

    created = 0
    skipped = 0
    created_codes: list[str] = []
    for kind, profile, length_mm in sorted(identities):
        code = _linear_code(meta["prefix"], profile, length_mm)
        if session.scalar(select(Product).where(Product.code == code)):
            skipped += 1
            continue
        # Collision identité (autre code, mêmes attrs kind/profile/length)
        existing = session.scalars(
            select(Product).where(
                Product.is_active.is_(True),
                Product.category == meta["category"],
            )
        ).all()
        dup = False
        for p in existing:
            pa = p.attributes or {}
            if (
                pa.get("kind") == kind
                and pa.get("profile") == profile
                and int(pa.get("length_mm") or 0) == length_mm
            ):
                dup = True
                break
        if dup:
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=f"{meta['label']} {profile} L.{length_mm} mm",
                category=meta["category"],
                subcategory=meta["subcategory"],
                reference_unit="pièce",
                description=f"{meta['label']} — identité kind/profile/longueur.",
                attributes={
                    "kind": kind,
                    "profile": profile,
                    "length_mm": length_mm,
                },
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        created_codes.append(code)
    if created:
        session.flush()
    return {
        "category": category_code,
        "identities_found": len(identities),
        "created": created,
        "skipped_existing": skipped,
        "created_codes": created_codes,
    }


def seed_panneau_mdf(session: Session) -> dict[str, Any]:
    rule = get_rule(CATEGORY_PANNEAU_MDF)
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    identities: set[tuple[int, int, int]] = set()
    for item in run.items:
        if not item.classified:
            continue
        if item.attrs.get("type") != "mdf":
            continue
        L, W, T = (
            item.attrs.get("length_mm"),
            item.attrs.get("width_mm"),
            item.attrs.get("thickness_mm"),
        )
        if L is None or W is None or T is None:
            continue
        identities.add((int(L), int(W), int(T)))

    created = 0
    skipped = 0
    created_codes: list[str] = []
    for L, W, T in sorted(identities):
        code = _mdf_code(L, W, T)
        if session.scalar(select(Product).where(Product.code == code)):
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=f"Panneau MDF {L} × {W} × {T} mm",
                category="Panneau",
                subcategory="Panneau MDF",
                reference_unit="pièce",
                description="Panneau MDF — identité L×l×Ép type=mdf.",
                attributes={
                    "type": "mdf",
                    "length_mm": L,
                    "width_mm": W,
                    "thickness_mm": T,
                },
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        created_codes.append(code)
    if created:
        session.flush()
    return {
        "category": CATEGORY_PANNEAU_MDF,
        "identities_found": len(identities),
        "created": created,
        "skipped_existing": skipped,
        "created_codes": created_codes,
    }


def seed_family(session: Session, category_code: str) -> dict[str, Any]:
    if category_code == CATEGORY_PANNEAU_MDF:
        return seed_panneau_mdf(session)
    return seed_linear_family(session, category_code)


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


def audit_exact_matches(session: Session, category_code: str) -> dict[str, Any]:
    rule = get_rule(category_code)
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    mismatches: list[dict[str, Any]] = []
    exact_ok = 0
    for item in run.items:
        if item.match is None or item.match.reason != REASON_EXACT:
            continue
        product = session.get(Product, item.match.product_id)
        if product is None:
            mismatches.append({"sp_id": item.sp.id, "reason": "product_missing"})
            continue
        pa = product.attributes or {}
        if category_code == CATEGORY_PANNEAU_MDF:
            keys = ("type", "length_mm", "width_mm", "thickness_mm")
            for k in keys:
                ev, tv = item.attrs.get(k), pa.get(k)
                if ev != tv and not (ev is not None and tv is not None and int(ev) == int(tv)):
                    if k == "type":
                        if ev != tv:
                            mismatches.append(
                                {
                                    "sp_id": item.sp.id,
                                    "key": k,
                                    "extracted": ev,
                                    "product": tv,
                                }
                            )
                            break
                    else:
                        mismatches.append(
                            {
                                "sp_id": item.sp.id,
                                "key": k,
                                "extracted": ev,
                                "product": tv,
                            }
                        )
                        break
            else:
                exact_ok += 1
        else:
            # LINEAR_PROFILE : nominal_length_mm ↔ product.length_mm
            if (
                item.attrs.get("kind") != pa.get("kind")
                or item.attrs.get("profile") != pa.get("profile")
                or int(item.attrs.get("nominal_length_mm") or -1)
                != int(pa.get("length_mm") or -2)
            ):
                mismatches.append(
                    {
                        "sp_id": item.sp.id,
                        "extracted": {
                            "kind": item.attrs.get("kind"),
                            "profile": item.attrs.get("profile"),
                            "nominal_length_mm": item.attrs.get("nominal_length_mm"),
                        },
                        "product": pa,
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
    return int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(SupplierProduct.product_id.is_not(None))
        )
        or 0
    )
