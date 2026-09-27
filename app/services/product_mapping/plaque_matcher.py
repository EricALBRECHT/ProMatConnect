"""Matching PLAQUE_PLATRE — caractéristiques techniques prioritaires.

UNKNOWN (null) ≠ false : une info absente ne crée pas d'EXACT artificiel.
Raisons V1.1 : EXACT / HIGH / REVIEW / AMBIGUOUS / NO_PMC_PRODUCT / INSUFFICIENT_DATA
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_PLAQUE_V1,
    PROPOSAL_EXACT,
    PROPOSAL_HIGH,
    PROPOSAL_REVIEW,
    PROPOSAL_UNMAPPED,
)
from app.services.product_mapping.plaque_extractor import (
    REASON_AMBIGUOUS,
    REASON_EXACT,
    REASON_HIGH,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
    REASON_REVIEW,
    dims_present,
    identity_attrs_sufficient,
)

IDENTITY_KEYS = ("length_mm", "width_mm", "thickness_mm", "type")
BOOL_COMPAT_KEYS = ("hydrofuge", "fire_resistant", "acoustic")


@dataclass(frozen=True)
class MatchResult:
    status: str
    product_id: int | None
    product_code: str | None
    score: float
    breakdown: dict[str, Any]
    reason: str = ""
    algorithm_version: str = ALGORITHM_VERSION_PLAQUE_V1


def _norm_product_attrs(raw: dict | None) -> dict[str, Any]:
    attrs = dict(raw or {})
    out: dict[str, Any] = {
        "length_mm": attrs.get("length_mm"),
        "width_mm": attrs.get("width_mm"),
        "thickness_mm": attrs.get("thickness_mm"),
        "type": attrs.get("type"),
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    ptype = out["type"]
    if ptype == "hydrofuge":
        out["hydrofuge"] = True
    elif ptype == "feu":
        out["fire_resistant"] = True
    elif ptype == "phonique":
        out["acoustic"] = True
    for key in BOOL_COMPAT_KEYS:
        if key in attrs and attrs[key] is not None:
            out[key] = bool(attrs[key])
    return out


def _bool_conflict(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    return bool(a) != bool(b)


def score_against_product(
    extracted: dict[str, Any],
    product_attrs: dict | None,
    *,
    product_id: int,
    product_code: str,
) -> MatchResult:
    target = _norm_product_attrs(product_attrs)
    breakdown: dict[str, Any] = {
        "extracted": extracted,
        "product": target,
        "identity_ok": [],
        "identity_missing": [],
        "identity_mismatch": [],
        "flag_conflicts": [],
    }

    for key in BOOL_COMPAT_KEYS:
        if _bool_conflict(extracted.get(key), target.get(key)):
            breakdown["flag_conflicts"].append(key)

    identity_complete = True
    identity_match = True
    for key in IDENTITY_KEYS:
        ev = extracted.get(key)
        tv = target.get(key)
        if ev is None or tv is None:
            identity_complete = False
            breakdown["identity_missing"].append(key)
            identity_match = False
            continue
        if ev != tv:
            identity_match = False
            breakdown["identity_mismatch"].append(
                {"key": key, "extracted": ev, "product": tv}
            )
        else:
            breakdown["identity_ok"].append(key)

    dims_ok = all(
        extracted.get(k) is not None
        and target.get(k) is not None
        and extracted.get(k) == target.get(k)
        for k in ("length_mm", "width_mm", "thickness_mm")
    )

    if breakdown["flag_conflicts"]:
        return MatchResult(
            status=PROPOSAL_REVIEW if dims_ok else PROPOSAL_UNMAPPED,
            product_id=product_id if dims_ok else None,
            product_code=product_code if dims_ok else None,
            score=0.2 if dims_ok else 0.0,
            breakdown=breakdown,
            reason=REASON_REVIEW if dims_ok else REASON_NO_PMC_PRODUCT,
        )

    if identity_complete and identity_match and not breakdown["flag_conflicts"]:
        return MatchResult(
            status=PROPOSAL_EXACT,
            product_id=product_id,
            product_code=product_code,
            score=1.0,
            breakdown=breakdown,
            reason=REASON_EXACT,
        )

    if dims_ok and extracted.get("type") and target.get("type"):
        if extracted["type"] == target["type"]:
            return MatchResult(
                status=PROPOSAL_HIGH,
                product_id=product_id,
                product_code=product_code,
                score=0.85,
                breakdown=breakdown,
                reason=REASON_HIGH,
            )
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown=breakdown,
            reason=REASON_NO_PMC_PRODUCT,
        )

    if dims_ok:
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=product_id,
            product_code=product_code,
            score=0.55,
            breakdown=breakdown,
            reason=REASON_REVIEW,
        )

    return MatchResult(
        status=PROPOSAL_UNMAPPED,
        product_id=None,
        product_code=None,
        score=0.0,
        breakdown=breakdown,
        reason=REASON_NO_PMC_PRODUCT,
    )


def best_match(
    extracted: dict[str, Any],
    candidates: list[tuple[int, str, dict | None]],
) -> MatchResult:
    if not dims_present(extracted):
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown={"reason": "insufficient_dims"},
            reason=REASON_INSUFFICIENT_DATA,
        )

    ranked: list[MatchResult] = []
    for pid, code, attrs in candidates:
        ranked.append(
            score_against_product(extracted, attrs, product_id=pid, product_code=code)
        )
    if not ranked:
        reason = (
            REASON_NO_PMC_PRODUCT
            if identity_attrs_sufficient(extracted)
            else REASON_INSUFFICIENT_DATA
        )
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown={"reason": "no_candidates"},
            reason=reason,
        )

    order = {
        PROPOSAL_EXACT: 0,
        PROPOSAL_HIGH: 1,
        PROPOSAL_REVIEW: 2,
        PROPOSAL_UNMAPPED: 3,
    }
    ranked.sort(key=lambda r: (order.get(r.status, 9), -r.score))
    best = ranked[0]

    top = [r for r in ranked if r.status == best.status and r.product_id is not None]
    if best.status in {PROPOSAL_EXACT, PROPOSAL_HIGH} and len(top) > 1:
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
        )

    if best.status == PROPOSAL_UNMAPPED or best.product_id is None:
        if identity_attrs_sufficient(extracted):
            return MatchResult(
                status=PROPOSAL_UNMAPPED,
                product_id=None,
                product_code=None,
                score=0.0,
                breakdown=best.breakdown,
                reason=REASON_NO_PMC_PRODUCT,
            )
        if not extracted.get("type"):
            return MatchResult(
                status=PROPOSAL_UNMAPPED,
                product_id=None,
                product_code=None,
                score=0.0,
                breakdown=best.breakdown,
                reason=REASON_INSUFFICIENT_DATA,
            )
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown=best.breakdown,
            reason=REASON_NO_PMC_PRODUCT,
        )

    return best
