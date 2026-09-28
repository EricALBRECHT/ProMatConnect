"""Seed + apply massif — familles DIMENSIONAL_FASTENER + BOARD_PANEL nouvelles
+ apply_exact des règles déjà enregistrées encore EXACT-ready.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, SupplierProduct
from app.services.product_mapping import pipeline
from app.services.product_mapping.fastener_batch import _code_from_pair, _name_for
from app.services.product_mapping.generic_matcher import REASON_EXACT
from app.services.product_mapping.mass_dimensional import (
    MASS_DIMENSIONAL_CODES,
    MASS_DIMENSIONAL_META,
)
from app.services.product_mapping.mass_panels import MASS_PANEL_CODES, MASS_PANEL_META
from app.services.product_mapping.rules import get_rule, registered_codes

EXISTING_REAPPLY = (
    "OSSATURE_PLACO",
    "VIS_BOIS",
    "VIS_PLACO",
    "VIS_AGGLO",
    "CHEVILLE_METAL",
    "ROND_ACIER",
    "CORNIERE_PVC",
    "FER_BETON",
    "PANNEAU_MDF",
    "TUBE_ROND_ACIER",
    "VIS_MULTI",
)


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
        target = rule.enrich_product_attrs(product.code, pa, product.subcategory)
        bad = False
        for ek in rule.identity_keys:
            tk = rule.product_key(ek)
            ev = item.attrs.get(ek)
            tv = target.get(tk)
            if ev is None or tv is None:
                bad = True
                mismatches.append(
                    {"sp_id": item.sp.id, "key": ek, "extracted": ev, "product": tv}
                )
                break
            try:
                if float(ev) == float(tv):
                    continue
            except (TypeError, ValueError):
                pass
            if ev != tv:
                bad = True
                mismatches.append(
                    {"sp_id": item.sp.id, "key": ek, "extracted": ev, "product": tv}
                )
                break
        if not bad:
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


def seed_dimensional(session: Session, category_code: str) -> dict[str, Any]:
    meta = MASS_DIMENSIONAL_META[category_code]
    rule = get_rule(category_code)
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    pairs: set[tuple[float, int]] = set()
    for item in run.items:
        if not item.classified:
            continue
        d, L = item.attrs.get("diameter_mm"), item.attrs.get("length_mm")
        if d is None or L is None:
            continue
        pairs.add((float(d), int(L)))

    created = 0
    skipped = 0
    codes: list[str] = []
    existing_types: set[tuple[float, int]] = set()
    for p in session.scalars(
        select(Product).where(Product.is_active.is_(True), Product.category == "Fixation")
    ):
        attrs = p.attributes or {}
        if attrs.get("type") != meta["type"]:
            continue
        if attrs.get("diameter_mm") is None or attrs.get("length_mm") is None:
            continue
        existing_types.add((float(attrs["diameter_mm"]), int(attrs["length_mm"])))

    for d, L in sorted(pairs):
        if (d, L) in existing_types:
            skipped += 1
            continue
        code = _code_from_pair(meta["prefix"], d, L)
        if session.scalar(select(Product).where(Product.code == code)):
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=_name_for(meta["label"], d, L),
                category=meta["category"],
                subcategory=meta["subcategory"],
                reference_unit="pièce",
                description=f"{meta['label']} {d}×{L} — type {meta['type']}.",
                attributes={
                    "type": meta["type"],
                    "diameter_mm": d,
                    "length_mm": L,
                },
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        codes.append(code)
        existing_types.add((d, L))
    if created:
        session.flush()
    return {
        "category": category_code,
        "identities_found": len(pairs),
        "created": created,
        "skipped_existing": skipped,
        "created_codes": codes,
    }


def seed_panel(session: Session, category_code: str) -> dict[str, Any]:
    meta = MASS_PANEL_META[category_code]
    rule = get_rule(category_code)
    run = pipeline.run_category(
        session, rule, persist=False, all_candidates=True, limit=None
    )
    triples: set[tuple[int, int, int]] = set()
    for item in run.items:
        if not item.classified:
            continue
        if item.attrs.get("type") != meta["type"]:
            continue
        L, W, T = (
            item.attrs.get("length_mm"),
            item.attrs.get("width_mm"),
            item.attrs.get("thickness_mm"),
        )
        if L is None or W is None or T is None:
            continue
        triples.add((int(L), int(W), int(T)))

    created = 0
    skipped = 0
    codes: list[str] = []
    for L, W, T in sorted(triples):
        code = f"{meta['prefix']}{L}X{W}X{T}"
        if session.scalar(select(Product).where(Product.code == code)):
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=f"{meta['label']} {L} × {W} × {T} mm",
                category=meta["category"],
                subcategory=meta["subcategory"],
                reference_unit="pièce",
                description=f"{meta['label']} — L×l×Ép type={meta['type']}.",
                attributes={
                    "type": meta["type"],
                    "length_mm": L,
                    "width_mm": W,
                    "thickness_mm": T,
                },
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        codes.append(code)
    if created:
        session.flush()
    return {
        "category": category_code,
        "identities_found": len(triples),
        "created": created,
        "skipped_existing": skipped,
        "created_codes": codes,
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


def count_mapped(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(SupplierProduct.product_id.is_not(None))
        )
        or 0
    )


def run_mass_pass(session: Session, *, apply: bool = True) -> dict[str, Any]:
    import app.services.product_mapping.mass_dimensional  # noqa: F401
    import app.services.product_mapping.mass_panels  # noqa: F401
    import app.services.product_mapping.rules  # noqa: F401

    report: dict[str, Any] = {
        "mapped_before": count_mapped(session),
        "families": [],
        "registered_codes": list(registered_codes()),
    }

    new_codes = list(MASS_DIMENSIONAL_CODES) + list(MASS_PANEL_CODES)

    for code in new_codes:
        fam: dict[str, Any] = {"category": code}
        if code in MASS_DIMENSIONAL_META:
            fam["seed"] = seed_dimensional(session, code)
        else:
            fam["seed"] = seed_panel(session, code)
        session.flush()
        fam["dry_run"] = dry_run_summary(session, code)
        fam["exact_audit"] = audit_exact_matches(session, code)
        if apply and fam["exact_audit"]["mismatch_count"] == 0:
            fam["apply"] = apply_if_clean(session, code)
            session.flush()
            fam["apply_second"] = apply_if_clean(session, code)
        elif apply:
            fam["apply"] = {
                "category": code,
                "applied": 0,
                "skipped_reason": "exact_mismatch",
                "audit": fam["exact_audit"],
            }
        report["families"].append(fam)

    for code in EXISTING_REAPPLY:
        if code not in registered_codes():
            continue
        fam = {"category": code, "reapply_existing": True}
        fam["dry_run"] = dry_run_summary(session, code)
        fam["exact_audit"] = audit_exact_matches(session, code)
        if apply and fam["exact_audit"]["mismatch_count"] == 0:
            fam["apply"] = apply_if_clean(session, code)
            session.flush()
            fam["apply_second"] = apply_if_clean(session, code)
        report["families"].append(fam)

    session.commit()
    report["mapped_after"] = count_mapped(session)
    return report
