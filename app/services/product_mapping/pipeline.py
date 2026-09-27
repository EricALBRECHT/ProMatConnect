"""Pipeline d'analyse générique — une seule boucle pour toutes les catégories.

Invariants, quelle que soit la famille :
- l'analyse n'écrit JAMAIS SupplierProduct.product_id (garde par snapshot) ;
- un mapping déjà posé est ALREADY_MAPPED, jamais recalculé ;
- l'application EXACT est transactionnelle et refuse toute identité UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Product, Supplier, SupplierProduct
from app.models.product_mapping import (
    CORRECTION_SOURCE_EXACT_RULE,
    PROPOSAL_EXACT,
    PROPOSAL_HIGH,
    PROPOSAL_REVIEW,
    ProductAttributeDef,
    ProductCategory,
    ProductMappingProposal,
    SupplierProductFeature,
)
from app.services.product_mapping.feature_set import (
    FeatureSet,
    feature_set_from_extraction,
)
from app.services.product_mapping.generic_matcher import (
    REASON_ALREADY_MAPPED,
    REASON_AMBIGUOUS,
    REASON_EXACT,
    REASON_HIGH,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
    REASON_NOT_THIS_CATEGORY,
    REASON_REVIEW,
    MatchResult,
    best_match,
)
from app.services.product_mapping.rules.base import CategoryRule

OUTCOME_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"
OUTCOME_ALREADY_MAPPED = "ALREADY_MAPPED"
OUTCOME_MATCHED = "MATCHED"


@dataclass
class CategoryItem:
    """Décision d'analyse pour un SupplierProduct."""

    sp: SupplierProduct
    outcome: str
    features: FeatureSet
    attrs: dict[str, Any]
    match: MatchResult | None = None
    reason: str = ""

    @property
    def classified(self) -> bool:
        return self.outcome != OUTCOME_NOT_THIS_CATEGORY


@dataclass
class CategoryRunResult:
    """Compteurs communs + décisions détaillées, adaptables par catégorie."""

    category_code: str = ""
    supplier_product_total: int = 0
    initial_candidates: int = 0
    analysed: int = 0
    not_this_category: int = 0
    classified: int = 0
    already_mapped: int = 0
    exact: int = 0
    high: int = 0
    review: int = 0
    ambiguous: int = 0
    no_pmc_product: int = 0
    insufficient_data: int = 0
    unmapped: int = 0
    persisted_features: int = 0
    persisted_proposals: int = 0
    items: list[CategoryItem] = field(default_factory=list)
    pmc_candidates: list[tuple] = field(default_factory=list)


@dataclass
class ExactApplication:
    """Proposition EXACT applicable (product_id encore NULL)."""

    supplier_product_id: int
    supplier_reference: str
    designation: str
    product_id: int
    product_code: str
    extracted: dict[str, Any]


@dataclass
class ApplyExactResult:
    analysed: int = 0
    already_mapped: int = 0
    exact_candidates: int = 0
    applied: int = 0
    skipped: int = 0
    errors: int = 0
    dry_run: bool = True
    applications: list[ExactApplication] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "analysed": self.analysed,
            "already_mapped": self.already_mapped,
            "exact_candidates": self.exact_candidates,
            "applied": self.applied,
            "skipped": self.skipped,
            "errors": self.errors,
            "dry_run": self.dry_run,
            "applications": [
                {
                    "supplier_product_id": a.supplier_product_id,
                    "supplier_reference": a.supplier_reference,
                    "designation": a.designation,
                    "product_id": a.product_id,
                    "product_code": a.product_code,
                    "extracted": a.extracted,
                }
                for a in self.applications
            ],
            "error_messages": self.error_messages,
        }


# --------------------------------------------------------------------------
# Schéma catégorie
# --------------------------------------------------------------------------

def ensure_category(session: Session, rule: CategoryRule) -> ProductCategory:
    """Crée (idempotent) la ProductCategory et ses ProductAttributeDef."""
    cat = session.scalar(
        select(ProductCategory).where(ProductCategory.code == rule.code)
    )
    if cat is None:
        cat = ProductCategory(
            code=rule.code,
            name=rule.category_name or rule.code,
            parent_id=None,
            reference_unit_default=rule.reference_unit_default,
            schema_version=rule.schema_version,
        )
        session.add(cat)
        session.flush()

    existing_keys = {
        d.key
        for d in session.scalars(
            select(ProductAttributeDef).where(ProductAttributeDef.category_id == cat.id)
        ).all()
    }
    for spec in rule.attribute_defs:
        if spec["key"] in existing_keys:
            continue
        session.add(
            ProductAttributeDef(
                category_id=cat.id,
                key=spec["key"],
                data_type=spec["data_type"],
                required=bool(spec.get("required")),
                unit=spec.get("unit"),
                enum_values=spec.get("enum_values"),
                match_role=spec.get("match_role", "optional"),
            )
        )
    session.flush()
    return cat


# --------------------------------------------------------------------------
# Sélecteurs
# --------------------------------------------------------------------------

def load_pmc_candidates(
    session: Session, rule: CategoryRule
) -> list[tuple[int, str, dict | None, str | None]]:
    """Produits PMC candidats, selon les sélecteurs déclarés par la règle."""
    clauses = [Product.code.like(f"{p}%") for p in rule.pmc_code_prefixes]
    clauses += [Product.category == c for c in rule.pmc_category_equals]
    clauses += [Product.subcategory == s for s in rule.pmc_subcategory_equals]
    if not clauses:
        return []
    rows = session.scalars(
        select(Product).where(or_(*clauses), Product.is_active.is_(True))
    ).all()
    return [(p.id, p.code, p.attributes, p.subcategory) for p in rows]


def resolve_supplier_ids(session: Session, supplier_name: str) -> list[int]:
    names = {supplier_name, "BRICO_DEPOT", "BRICO DEPOT", "Brico Dépôt"}
    suppliers = session.scalars(select(Supplier).where(Supplier.name.in_(names))).all()
    return [s.id for s in suppliers]


def sql_supplier_candidates(
    session: Session,
    rule: CategoryRule,
    supplier_ids: list[int],
    *,
    limit: int | None = None,
) -> list[SupplierProduct]:
    """Pré-sélection SQL (évite de scanner tout le catalogue fournisseur)."""
    filters = [
        SupplierProduct.designation.ilike(f"%{p}%") for p in rule.designation_ilike
    ]
    q = (
        select(SupplierProduct)
        .where(
            SupplierProduct.supplier_id.in_(supplier_ids),
            or_(*filters),
        )
        .order_by(SupplierProduct.id)
    )
    if limit is not None:
        q = q.limit(limit)
    return list(session.scalars(q).all())


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------

def extract_features(rule: CategoryRule, sp: SupplierProduct) -> FeatureSet:
    """Extraction catégorie → FeatureSet normalisé selon la règle."""
    extraction = rule.extract(sp.designation or "")  # type: ignore[misc]
    if isinstance(extraction, FeatureSet):
        return extraction
    return feature_set_from_extraction(extraction, norms=rule.normalizations)


def _normalize_reason(match: MatchResult) -> str:
    reason = match.reason or ""
    if reason:
        return reason
    if match.status == PROPOSAL_EXACT:
        return REASON_EXACT
    if match.status == PROPOSAL_HIGH:
        return REASON_HIGH
    if match.status == PROPOSAL_REVIEW:
        return REASON_REVIEW
    return REASON_NO_PMC_PRODUCT


_KNOWN_REASONS = {
    REASON_EXACT,
    REASON_HIGH,
    REASON_AMBIGUOUS,
    REASON_REVIEW,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
}


def run_category(
    session: Session,
    rule: CategoryRule,
    *,
    supplier_name: str = "BRICO_DEPOT",
    limit: int | None = 50,
    all_candidates: bool = False,
    example_limit: int = 10,
    persist: bool = False,
    dry_run: bool = True,
) -> CategoryRunResult:
    """Analyse dry-run d'une catégorie — ne modifie jamais product_id."""
    ensure_category(session, rule)
    result = CategoryRunResult(category_code=rule.code)
    result.pmc_candidates = load_pmc_candidates(session, rule)

    supplier_ids = resolve_supplier_ids(session, supplier_name)
    if not supplier_ids:
        return result

    result.supplier_product_total = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(SupplierProduct.supplier_id.in_(supplier_ids))
        )
        or 0
    )

    # --all : toute la pré-sélection SQL ; sinon plafond large pour atteindre --limit
    fetch_limit = None if all_candidates else max((limit or 50) * 20, 200)
    sps = sql_supplier_candidates(session, rule, supplier_ids, limit=fetch_limit)
    result.initial_candidates = len(sps)
    snapshot_product_ids = {sp.id: sp.product_id for sp in sps}

    for sp in sps:
        if not all_candidates and limit is not None and result.classified >= limit:
            break

        features = extract_features(rule, sp)
        result.analysed += 1

        if not features.classified:
            result.not_this_category += 1
            result.items.append(
                CategoryItem(
                    sp=sp,
                    outcome=OUTCOME_NOT_THIS_CATEGORY,
                    features=features,
                    attrs={},
                    reason=features.reason or REASON_NOT_THIS_CATEGORY,
                )
            )
            continue

        result.classified += 1
        attrs = features.as_attrs()

        # Mapping déjà posé (manuel ou historique) — intouchable
        if sp.product_id is not None:
            result.already_mapped += 1
            result.items.append(
                CategoryItem(
                    sp=sp,
                    outcome=OUTCOME_ALREADY_MAPPED,
                    features=features,
                    attrs=attrs,
                    reason=REASON_ALREADY_MAPPED,
                )
            )
            continue

        match = best_match(rule, attrs, result.pmc_candidates)
        reason = _normalize_reason(match)
        if reason not in _KNOWN_REASONS:
            reason = REASON_NO_PMC_PRODUCT

        if reason == REASON_EXACT:
            result.exact += 1
        elif reason == REASON_HIGH:
            result.high += 1
        elif reason == REASON_AMBIGUOUS:
            result.ambiguous += 1
        elif reason == REASON_REVIEW:
            result.review += 1
        elif reason == REASON_INSUFFICIENT_DATA:
            result.insufficient_data += 1
            result.unmapped += 1
        else:
            result.no_pmc_product += 1
            result.unmapped += 1

        result.items.append(
            CategoryItem(
                sp=sp,
                outcome=OUTCOME_MATCHED,
                features=features,
                attrs=attrs,
                match=match,
                reason=reason,
            )
        )

        if persist and not dry_run:
            upsert_feature(session, rule, sp.id, features)
            upsert_proposal(session, rule, sp.id, match)
            result.persisted_features += 1
            result.persisted_proposals += 1

    for sp_id, old_pid in snapshot_product_ids.items():
        sp = session.get(SupplierProduct, sp_id)
        if sp is not None and sp.product_id != old_pid:
            raise RuntimeError(
                f"Analyse dry-run {rule.code} ne doit jamais modifier "
                "SupplierProduct.product_id"
            )

    if persist and not dry_run:
        session.flush()
    return result


# --------------------------------------------------------------------------
# Persistance features / propositions
# --------------------------------------------------------------------------

def upsert_feature(
    session: Session, rule: CategoryRule, sp_id: int, features: FeatureSet
) -> None:
    row = session.scalar(
        select(SupplierProductFeature).where(
            SupplierProductFeature.supplier_product_id == sp_id
        )
    )
    conf = (
        Decimal(str(features.confidence)) if features.confidence is not None else None
    )
    category_code = features.category_code or rule.code
    extractor_version = features.extractor_version or rule.extractor_version
    if row is None:
        session.add(
            SupplierProductFeature(
                supplier_product_id=sp_id,
                category_code=category_code,
                attributes=features.as_attrs(),
                extractor_version=extractor_version,
                confidence=conf,
            )
        )
    else:
        row.category_code = category_code
        row.attributes = features.as_attrs()
        row.extractor_version = features.extractor_version
        row.confidence = conf


def upsert_proposal(
    session: Session, rule: CategoryRule, sp_id: int, match: MatchResult
) -> None:
    session.add(
        ProductMappingProposal(
            supplier_product_id=sp_id,
            product_id=match.product_id,
            status=match.status,
            score=Decimal(str(match.score)) if match.score is not None else None,
            score_breakdown={**(match.breakdown or {}), "reason": match.reason},
            algorithm_version=match.algorithm_version or rule.algorithm_version,
        )
    )


# --------------------------------------------------------------------------
# Application contrôlée des EXACT
# --------------------------------------------------------------------------

def is_exact_applicable(
    rule: CategoryRule,
    *,
    sp: Any,
    extraction_attrs: dict[str, Any],
    match: MatchResult,
) -> bool:
    """True si l'EXACT est applicable sans ambiguïté ni UNKNOWN sur l'identité."""
    if getattr(sp, "product_id", None) is not None:
        return False
    if match.reason != REASON_EXACT or match.status != PROPOSAL_EXACT:
        return False
    if match.product_id is None or not match.product_code:
        return False
    # Identité complète — pas d'EXACT construit sur UNKNOWN
    return rule.identity_complete(extraction_attrs)


def apply_exact_for_rule(
    session: Session,
    rule: CategoryRule,
    *,
    supplier_name: str = "BRICO_DEPOT",
    dry_run: bool = True,
) -> ApplyExactResult:
    """Applique les mappings EXACT éligibles (transactionnel).

    dry_run=True (défaut) : liste uniquement, aucune écriture product_id.
    dry_run=False : écrit product_id + correction_source=exact_rule.
    """
    ensure_category(session, rule)
    candidates = load_pmc_candidates(session, rule)
    out = ApplyExactResult(dry_run=dry_run)

    supplier_ids = resolve_supplier_ids(session, supplier_name)
    if not supplier_ids:
        return out

    sps = sql_supplier_candidates(session, rule, supplier_ids, limit=None)
    snapshot = {sp.id: (sp.product_id, sp.correction_source) for sp in sps}

    def _run_apply() -> None:
        for sp in sps:
            features = extract_features(rule, sp)
            out.analysed += 1
            if not features.classified:
                continue

            if sp.product_id is not None:
                out.already_mapped += 1
                continue

            attrs = features.as_attrs()
            match = best_match(rule, attrs, candidates)
            if not is_exact_applicable(
                rule, sp=sp, extraction_attrs=attrs, match=match
            ):
                out.skipped += 1
                continue

            assert match.product_id is not None and match.product_code
            out.exact_candidates += 1
            out.applications.append(
                ExactApplication(
                    supplier_product_id=sp.id,
                    supplier_reference=sp.supplier_reference,
                    designation=(sp.designation or "")[:200],
                    product_id=match.product_id,
                    product_code=match.product_code,
                    extracted=dict(attrs),
                )
            )

            if dry_run:
                continue

            # Protections runtime
            if sp.product_id is not None:
                out.skipped += 1
                out.exact_candidates -= 1
                out.applications.pop()
                continue
            sp.product_id = match.product_id
            sp.correction_source = CORRECTION_SOURCE_EXACT_RULE
            from app.services.conditioning import resolve_reference_quantity

            sp.reference_quantity = resolve_reference_quantity(sp)
            out.applied += 1

        if not dry_run:
            session.flush()
            # Vérif post-écriture : seuls les appliqués ont changé
            applied_ids = {a.supplier_product_id for a in out.applications}
            for sp_id, (old_pid, old_src) in snapshot.items():
                sp = session.get(SupplierProduct, sp_id)
                if sp is None:
                    continue
                if sp_id in applied_ids:
                    if (
                        sp.product_id is None
                        or sp.correction_source != CORRECTION_SOURCE_EXACT_RULE
                    ):
                        raise RuntimeError(
                            f"Application EXACT incomplète pour SP {sp_id}"
                        )
                elif sp.product_id != old_pid or sp.correction_source != old_src:
                    raise RuntimeError(f"SP {sp_id} modifié hors périmètre EXACT")

    try:
        if dry_run:
            _run_apply()
        else:
            with session.begin_nested():
                _run_apply()
    except Exception as exc:
        out.errors += 1
        out.error_messages.append(str(exc))
        if not dry_run:
            out.applied = 0
        raise

    return out
