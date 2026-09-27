"""Matcher générique piloté par CategoryRule.

UNKNOWN (null) ≠ false : une information absente ne fabrique jamais d'EXACT.
Raisons : EXACT / HIGH / REVIEW / AMBIGUOUS / NO_PMC_PRODUCT / INSUFFICIENT_DATA
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.models.product_mapping import (
    PROPOSAL_EXACT,
    PROPOSAL_HIGH,
    PROPOSAL_REVIEW,
    PROPOSAL_UNMAPPED,
)
from app.services.product_mapping.rules.base import (
    ALGORITHM_VERSION_GENERIC_V2,
    PARTIAL_HIERARCHY,
    CategoryRule,
)

REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"
REASON_NO_PMC_PRODUCT = "NO_PMC_PRODUCT"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REASON_AMBIGUOUS = "AMBIGUOUS"
REASON_ALREADY_MAPPED = "ALREADY_MAPPED"
REASON_EXACT = "EXACT"
REASON_HIGH = "HIGH"
REASON_REVIEW = "REVIEW"

_STATUS_ORDER = {
    PROPOSAL_EXACT: 0,
    PROPOSAL_HIGH: 1,
    PROPOSAL_REVIEW: 2,
    PROPOSAL_UNMAPPED: 3,
}

# Candidat PMC : (id, code, attributes, subcategory)
Candidate = tuple[int, str, dict | None, str | None]


@dataclass(frozen=True)
class MatchResult:
    status: str
    product_id: int | None
    product_code: str | None
    score: float
    breakdown: dict[str, Any]
    reason: str = ""
    algorithm_version: str = ALGORITHM_VERSION_GENERIC_V2


def _unmapped(rule: CategoryRule, breakdown: dict, reason: str) -> MatchResult:
    return MatchResult(
        status=PROPOSAL_UNMAPPED,
        product_id=None,
        product_code=None,
        score=0.0,
        breakdown=breakdown,
        reason=reason,
        algorithm_version=rule.algorithm_version,
    )


def _bool_conflict(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    return bool(a) != bool(b)


def _dims_match(rule: CategoryRule, extracted: Mapping, target: Mapping) -> bool:
    if not rule.dim_keys_for_partial:
        return False
    for key in rule.dim_keys_for_partial:
        ev = extracted.get(key)
        tv = target.get(rule.product_key(key))
        if ev is None or tv is None or ev != tv:
            return False
    return True


def score_against_product(
    rule: CategoryRule,
    extracted: dict[str, Any],
    *,
    product_id: int,
    product_code: str,
    product_attrs: dict | None = None,
    subcategory: str | None = None,
) -> MatchResult:
    """Confronte un jeu d'attributs extraits à un produit PMC candidat."""
    target = rule.enrich_product_attrs(product_code, product_attrs, subcategory)
    breakdown: dict[str, Any] = {
        "extracted": extracted,
        "product": target,
        "identity_ok": [],
        "identity_missing": [],
        "identity_mismatch": [],
    }

    flag_conflicts: list[str] = []
    if rule.optional_compat_keys:
        for key in rule.optional_compat_keys:
            if _bool_conflict(extracted.get(key), target.get(rule.product_key(key))):
                flag_conflicts.append(key)
        breakdown["flag_conflicts"] = flag_conflicts

    identity_complete = True
    identity_match = True
    for ek in rule.identity_keys:
        tk = rule.product_key(ek)
        ev = extracted.get(ek)
        tv = target.get(tk)
        if ev is None or tv is None:
            identity_complete = False
            identity_match = False
            breakdown["identity_missing"].append({"key": ek, "product_key": tk})
            continue
        if ev != tv:
            identity_match = False
            breakdown["identity_mismatch"].append(
                {"key": ek, "product_key": tk, "extracted": ev, "product": tv}
            )
        else:
            breakdown["identity_ok"].append({"key": ek, "product_key": tk})

    dims_ok = _dims_match(rule, extracted, target)

    # Incompatibilité fonctionnelle connue des deux côtés → jamais EXACT
    if flag_conflicts:
        if dims_ok:
            return MatchResult(
                status=PROPOSAL_REVIEW,
                product_id=product_id,
                product_code=product_code,
                score=0.2,
                breakdown=breakdown,
                reason=REASON_REVIEW,
                algorithm_version=rule.algorithm_version,
            )
        return _unmapped(rule, breakdown, REASON_NO_PMC_PRODUCT)

    if identity_complete and identity_match:
        return MatchResult(
            status=PROPOSAL_EXACT,
            product_id=product_id,
            product_code=product_code,
            score=1.0,
            breakdown=breakdown,
            reason=REASON_EXACT,
            algorithm_version=rule.algorithm_version,
        )

    if rule.partial_strategy == PARTIAL_HIERARCHY:
        return _hierarchy_fallback(
            rule, extracted, target, breakdown, product_id, product_code
        )
    return _dims_fallback(
        rule, extracted, target, breakdown, dims_ok, product_id, product_code
    )


def _dims_fallback(
    rule: CategoryRule,
    extracted: Mapping,
    target: Mapping,
    breakdown: dict,
    dims_ok: bool,
    product_id: int,
    product_code: str,
) -> MatchResult:
    """Repli « dimensions » : dimensions identiques → HIGH (même type) ou REVIEW."""
    type_key = rule.high_type_key
    if rule.allow_high and dims_ok and type_key:
        ev = extracted.get(type_key)
        tv = target.get(rule.product_key(type_key))
        if ev and tv:
            if ev == tv:
                return MatchResult(
                    status=PROPOSAL_HIGH,
                    product_id=product_id,
                    product_code=product_code,
                    score=0.85,
                    breakdown=breakdown,
                    reason=REASON_HIGH,
                    algorithm_version=rule.algorithm_version,
                )
            # Même géométrie mais variante fonctionnelle différente → pas ce PMC
            return _unmapped(rule, breakdown, REASON_NO_PMC_PRODUCT)

    if dims_ok:
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=product_id,
            product_code=product_code,
            score=0.55,
            breakdown=breakdown,
            reason=REASON_REVIEW,
            algorithm_version=rule.algorithm_version,
        )
    return _unmapped(rule, breakdown, REASON_NO_PMC_PRODUCT)


def _hierarchy_fallback(
    rule: CategoryRule,
    extracted: Mapping,
    target: Mapping,
    breakdown: dict,
    product_id: int,
    product_code: str,
) -> MatchResult:
    """Repli « hiérarchie » : clés discriminantes ordonnées (ex. kind → profile → L)."""
    head = rule.hierarchy_keys
    discriminant = rule.identity_keys[-1] if rule.identity_keys else None

    head_equal = bool(head) and all(
        extracted.get(k) is not None
        and target.get(rule.product_key(k)) is not None
        and extracted.get(k) == target.get(rule.product_key(k))
        for k in head
    )
    if head_equal and discriminant:
        ev = extracted.get(discriminant)
        tv = target.get(rule.product_key(discriminant))
        if ev is not None and tv is not None and ev != tv:
            # Même famille, valeur discriminante absente du catalogue PMC
            return _unmapped(rule, breakdown, REASON_NO_PMC_PRODUCT)

    anchor = head[0] if head else None
    if (
        anchor
        and extracted.get(anchor) is not None
        and target.get(rule.product_key(anchor)) is not None
        and extracted.get(anchor) == target.get(rule.product_key(anchor))
    ):
        secondary = head[1] if len(head) > 1 else None
        aligned = secondary is None or extracted.get(secondary) == target.get(
            rule.product_key(secondary)
        )
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=product_id if aligned else None,
            product_code=product_code if aligned else None,
            score=0.3,
            breakdown=breakdown,
            reason=REASON_REVIEW,
            algorithm_version=rule.algorithm_version,
        )

    return _unmapped(rule, breakdown, REASON_NO_PMC_PRODUCT)


def _as_candidate(raw: Sequence) -> Candidate:
    if len(raw) == 4:
        return raw  # type: ignore[return-value]
    pid, code, attrs = raw
    return (pid, code, attrs, None)


def has_required_input(rule: CategoryRule, extracted: Mapping[str, Any]) -> bool:
    """Données minimales pour tenter un match (sinon INSUFFICIENT_DATA)."""
    if rule.dim_keys_for_partial:
        return all(extracted.get(k) is not None for k in rule.dim_keys_for_partial)
    return rule.identity_complete(extracted)


def best_match(
    rule: CategoryRule,
    extracted: dict[str, Any],
    candidates: Sequence[Sequence],
) -> MatchResult:
    """Meilleur candidat PMC, ou UNMAPPED avec la raison explicite du blocage."""
    if not has_required_input(rule, extracted):
        marker = (
            "insufficient_dims" if rule.dim_keys_for_partial else "insufficient_identity"
        )
        return _unmapped(rule, {"reason": marker}, REASON_INSUFFICIENT_DATA)

    ranked = [
        score_against_product(
            rule,
            extracted,
            product_id=pid,
            product_code=code,
            product_attrs=attrs,
            subcategory=sub,
        )
        for pid, code, attrs, sub in (_as_candidate(c) for c in candidates)
    ]
    if not ranked:
        reason = (
            REASON_NO_PMC_PRODUCT
            if rule.identity_complete(extracted)
            else REASON_INSUFFICIENT_DATA
        )
        return _unmapped(rule, {"reason": "no_candidates"}, reason)

    ranked.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 9), -r.score))
    best = ranked[0]

    ambiguous_statuses = (
        {PROPOSAL_EXACT, PROPOSAL_HIGH} if rule.allow_high else {PROPOSAL_EXACT}
    )
    top = [r for r in ranked if r.status == best.status and r.product_id is not None]
    if best.status in ambiguous_statuses and len(top) > 1:
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=best.product_id,
            product_code=best.product_code,
            score=best.score * 0.7,
            breakdown={
                "reason": "ambiguous_candidates",
                "candidates": [
                    {
                        "product_id": r.product_id,
                        "code": r.product_code,
                        "score": r.score,
                    }
                    for r in top
                ],
                "primary": best.breakdown,
            },
            reason=REASON_AMBIGUOUS,
            algorithm_version=rule.algorithm_version,
        )

    if best.status == PROPOSAL_UNMAPPED or best.product_id is None:
        reason = (
            REASON_NO_PMC_PRODUCT
            if rule.identity_complete(extracted)
            else REASON_INSUFFICIENT_DATA
        )
        return _unmapped(rule, best.breakdown, reason)

    return best
