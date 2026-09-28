"""Rattachement Gedimat → Product PMC.

Ordre : EAN exact, règles d'identité déjà enregistrées (EXACT seulement),
puis fallback spécifique. Aucun SupplierProduct déjà mappé n'est réécrit.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.gedimat.connector import SUPPLIER_NAME
from app.models import Supplier, SupplierProduct
from app.models.product_mapping import (
    CORRECTION_SOURCE_EAN,
    CORRECTION_SOURCE_EXACT_RULE,
    SupplierProductFeature,
)
from app.services.gedimat_catalog_import import FEATURE_CATEGORY
from app.services.product_mapping.mass_specific import attach_specific
from app.services.product_mapping.pipeline import (
    extract_features,
    is_exact_applicable,
    load_pmc_candidates,
)
from app.services.product_mapping.rules import get_rule, registered_codes
from app.services.product_mapping.rules.base import CategoryRule


def map_gedimat_catalog(session: Session) -> dict[str, int]:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == SUPPLIER_NAME))
    if supplier_id is None:
        return {"ean": 0, "rules": 0, "specific_created": 0, "specific_mapped": 0, "ambiguous": 0}
    ean = attach_by_ean(session, supplier_id)
    rules, ambiguous = attach_by_existing_rules(session, supplier_id)
    specific = attach_specific(session, supplier_name=SUPPLIER_NAME)
    return {
        "ean": ean,
        "rules": rules,
        "ambiguous": ambiguous,
        "specific_created": specific["created"],
        "specific_mapped": specific["mapped"],
    }


def attach_by_ean(session: Session, supplier_id: int) -> int:
    """Même EAN qu'un SupplierProduct déjà rattaché, hors fournisseurs TEST."""
    owners = session.execute(
        select(SupplierProduct.ean, SupplierProduct.product_id, Supplier.name)
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .where(
            SupplierProduct.product_id.is_not(None),
            SupplierProduct.ean.is_not(None),
            SupplierProduct.supplier_id != supplier_id,
        )
    ).all()
    by_ean: dict[str, set[int]] = defaultdict(set)
    for ean, product_id, name in owners:
        if str(name or "").rstrip().endswith(" TEST"):
            continue
        digits = "".join(ch for ch in str(ean or "") if ch.isdigit())
        if len(digits) not in {8, 12, 13, 14} or product_id is None:
            continue
        by_ean[digits].add(int(product_id))
    unique = {ean: next(iter(ids)) for ean, ids in by_ean.items() if len(ids) == 1}

    pending = session.scalars(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.product_id.is_(None),
            SupplierProduct.ean.is_not(None),
        )
    ).all()
    mapped = 0
    for sp in pending:
        digits = "".join(ch for ch in str(sp.ean or "") if ch.isdigit())
        product_id = unique.get(digits)
        if product_id is None:
            continue
        sp.product_id = product_id
        sp.correction_source = CORRECTION_SOURCE_EAN
        mapped += 1
    if mapped:
        session.flush()
    return mapped


def attach_by_existing_rules(session: Session, supplier_id: int) -> tuple[int, int]:
    """Réutilise les CategoryRule enregistrées. EXACT seulement, sinon rien."""
    features = {
        row.supplier_product_id: row.attributes or {}
        for row in session.scalars(
            select(SupplierProductFeature).where(
                SupplierProductFeature.category_code == FEATURE_CATEGORY
            )
        ).all()
    }
    claims: dict[int, set[int]] = defaultdict(set)
    for code in registered_codes():
        rule = get_rule(code)
        if not rule.designation_ilike:
            continue
        candidates = load_pmc_candidates(session, rule)
        if not candidates:
            continue
        rows = _rule_candidates(session, supplier_id, rule)
        for sp in rows:
            if sp.product_id is not None:
                continue
            text = _match_text(sp, features.get(sp.id))
            try:
                if text == (sp.designation or ""):
                    features_set = extract_features(rule, sp)
                else:
                    features_set = _extract_text(rule, text)
            except Exception:
                continue
            if not features_set.classified:
                continue
            attrs = features_set.as_attrs()
            match = _best(rule, attrs, candidates)
            if not is_exact_applicable(rule, sp=sp, extraction_attrs=attrs, match=match):
                continue
            if match.product_id is not None:
                claims[sp.id].add(int(match.product_id))

    pending = {
        sp.id: sp
        for sp in session.scalars(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.product_id.is_(None),
            )
        ).all()
    }
    mapped = 0
    ambiguous = 0
    for sp_id, product_ids in claims.items():
        sp = pending.get(sp_id)
        if sp is None or sp.product_id is not None:
            continue
        if len(product_ids) != 1:
            ambiguous += 1
            continue
        sp.product_id = next(iter(product_ids))
        sp.correction_source = CORRECTION_SOURCE_EXACT_RULE
        mapped += 1
    if mapped:
        session.flush()
    return mapped, ambiguous


def gedimat_coverage(session: Session) -> dict[str, int]:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == SUPPLIER_NAME))
    brico_id = session.scalar(select(Supplier.id).where(Supplier.name == "BRICO_DEPOT"))
    if supplier_id is None:
        return {}
    total = int(
        session.scalar(
            select(func.count()).select_from(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier_id
            )
        )
        or 0
    )
    mapped = int(
        session.scalar(
            select(func.count()).select_from(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.product_id.is_not(None),
            )
        )
        or 0
    )
    active = int(
        session.scalar(
            select(func.count()).select_from(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.active.is_(True),
            )
        )
        or 0
    )
    from sqlalchemy import text

    duplicate_skus = int(
        session.execute(
            text(
                """
                SELECT count(*) FROM (
                    SELECT supplier_reference
                    FROM supplier_products
                    WHERE supplier_id = :supplier_id
                    GROUP BY supplier_reference
                    HAVING count(*) > 1
                ) duplicates
                """
            ),
            {"supplier_id": supplier_id},
        ).scalar()
        or 0
    )
    by_source = dict(
        session.execute(
            select(SupplierProduct.correction_source, func.count())
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.product_id.is_not(None),
            )
            .group_by(SupplierProduct.correction_source)
        ).all()
    )
    shared = 0
    if brico_id is not None:
        brico_products = select(SupplierProduct.product_id).where(
            SupplierProduct.supplier_id == brico_id,
            SupplierProduct.product_id.is_not(None),
        )
        shared = int(
            session.scalar(
                select(func.count(func.distinct(SupplierProduct.product_id))).where(
                    SupplierProduct.supplier_id == supplier_id,
                    SupplierProduct.product_id.in_(brico_products),
                )
            )
            or 0
        )
    return {
        "total": total,
        "active": active,
        "mapped": mapped,
        "unmapped": total - mapped,
        "duplicate_skus": duplicate_skus,
        "ean": int(by_source.get(CORRECTION_SOURCE_EAN, 0)),
        "rules": int(by_source.get(CORRECTION_SOURCE_EXACT_RULE, 0)),
        "specific": int(by_source.get("specific", 0)),
        "shared_with_brico": shared,
    }


def _rule_candidates(
    session: Session, supplier_id: int, rule: CategoryRule
) -> list[SupplierProduct]:
    from sqlalchemy import or_

    filters = [SupplierProduct.designation.ilike(f"%{token}%") for token in rule.designation_ilike]
    return list(
        session.scalars(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.product_id.is_(None),
                SupplierProduct.active.is_(True),
                or_(*filters),
            )
        ).all()
    )


def _match_text(sp: SupplierProduct, attributes: dict | None) -> str:
    base = sp.designation or ""
    properties = (attributes or {}).get("properties")
    if not isinstance(properties, dict) or not properties:
        return base
    parts: list[str] = []
    for value in properties.values():
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))
    extra = " ".join(parts).strip()
    if not extra:
        return base
    return f"{base} {extra}"


def _extract_text(rule: CategoryRule, text: str):
    extraction = rule.extract(text)  # type: ignore[misc]
    from app.services.product_mapping.feature_set import feature_set_from_extraction
    from app.services.product_mapping.feature_set import FeatureSet

    if isinstance(extraction, FeatureSet):
        return extraction
    return feature_set_from_extraction(extraction, norms=rule.normalizations)


def _best(rule: CategoryRule, attrs: dict[str, Any], candidates: list):
    from app.services.product_mapping.generic_matcher import best_match

    return best_match(rule, attrs, candidates)
