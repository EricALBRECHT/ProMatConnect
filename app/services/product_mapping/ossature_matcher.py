"""Matching OSSATURE_PLACO — identité kind + profile + nominal_length_mm.

length_mm fournisseur conservé tel quel ; matching via nominal_length_mm
contre Product.attributes.length_mm (longueur catalogue nominale).
Table nominale explicite — pas de tolérance ± / round.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_OSSATURE_V1,
    KIND_FOURRURE,
    KIND_MONTANT,
    KIND_RAIL,
    PROPOSAL_EXACT,
    PROPOSAL_REVIEW,
    PROPOSAL_UNMAPPED,
)
from app.services.product_mapping.ossature_extractor import (
    REASON_AMBIGUOUS,
    REASON_EXACT,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
    REASON_REVIEW,
    identity_attrs_sufficient,
)

# Clés extraites ↔ clés Product
# extracted.nominal_length_mm == product.length_mm
EXTRACTED_IDENTITY = ("kind", "profile", "nominal_length_mm")
PRODUCT_IDENTITY = ("kind", "profile", "length_mm")


@dataclass(frozen=True)
class MatchResult:
    status: str
    product_id: int | None
    product_code: str | None
    score: float
    breakdown: dict[str, Any]
    reason: str = ""
    algorithm_version: str = ALGORITHM_VERSION_OSSATURE_V1


def kind_from_product(code: str, attrs: dict | None, subcategory: str | None = None) -> str | None:
    c = (code or "").upper()
    if c.startswith("PMC-RAIL-"):
        return KIND_RAIL
    if c.startswith("PMC-MONTANT-"):
        return KIND_MONTANT
    if c.startswith("PMC-FOURRURE-"):
        return KIND_FOURRURE
    sub = (subcategory or "").lower()
    if "rail" in sub:
        return KIND_RAIL
    if "montant" in sub:
        return KIND_MONTANT
    if "fourrure" in sub:
        return KIND_FOURRURE
    if attrs and attrs.get("kind"):
        return str(attrs["kind"]).upper()
    return None


def _product_identity(
    code: str, attrs: dict | None, subcategory: str | None = None
) -> dict[str, Any]:
    a = dict(attrs or {})
    return {
        "kind": kind_from_product(code, a, subcategory),
        "profile": a.get("profile"),
        "length_mm": a.get("length_mm"),
    }


def score_against_product(
    extracted: dict[str, Any],
    *,
    product_id: int,
    product_code: str,
    product_attrs: dict | None,
    subcategory: str | None = None,
) -> MatchResult:
    target = _product_identity(product_code, product_attrs, subcategory)
    breakdown: dict[str, Any] = {
        "extracted": extracted,
        "product": target,
        "identity_ok": [],
        "identity_missing": [],
        "identity_mismatch": [],
    }

    pairs = (
        ("kind", "kind"),
        ("profile", "profile"),
        ("nominal_length_mm", "length_mm"),
    )
    identity_complete = True
    identity_match = True
    for ek, tk in pairs:
        ev = extracted.get(ek)
        tv = target.get(tk)
        if ev is None or tv is None:
            identity_complete = False
            breakdown["identity_missing"].append({"extracted": ek, "product": tk})
            identity_match = False
            continue
        if ev != tv:
            identity_match = False
            breakdown["identity_mismatch"].append(
                {"extracted_key": ek, "extracted": ev, "product_key": tk, "product": tv}
            )
        else:
            breakdown["identity_ok"].append({"extracted": ek, "product": tk})

    if identity_complete and identity_match:
        return MatchResult(
            status=PROPOSAL_EXACT,
            product_id=product_id,
            product_code=product_code,
            score=1.0,
            breakdown=breakdown,
            reason=REASON_EXACT,
        )

    # Même kind+profile, longueur nominale différente → NO_PMC
    if (
        extracted.get("kind")
        and target.get("kind")
        and extracted.get("kind") == target.get("kind")
        and extracted.get("profile")
        and target.get("profile")
        and extracted.get("profile") == target.get("profile")
        and extracted.get("nominal_length_mm") is not None
        and target.get("length_mm") is not None
        and extracted.get("nominal_length_mm") != target.get("length_mm")
    ):
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown=breakdown,
            reason=REASON_NO_PMC_PRODUCT,
        )

    if extracted.get("kind") and target.get("kind") and extracted["kind"] == target["kind"]:
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=product_id if extracted.get("profile") == target.get("profile") else None,
            product_code=product_code if extracted.get("profile") == target.get("profile") else None,
            score=0.3,
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
    candidates: list[tuple[int, str, dict | None, str | None]],
) -> MatchResult:
    if not identity_attrs_sufficient(extracted):
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown={"reason": "insufficient_identity"},
            reason=REASON_INSUFFICIENT_DATA,
        )

    ranked: list[MatchResult] = []
    for pid, code, attrs, sub in candidates:
        ranked.append(
            score_against_product(
                extracted,
                product_id=pid,
                product_code=code,
                product_attrs=attrs,
                subcategory=sub,
            )
        )
    if not ranked:
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown={"reason": "no_candidates"},
            reason=REASON_NO_PMC_PRODUCT,
        )

    order = {PROPOSAL_EXACT: 0, PROPOSAL_REVIEW: 1, PROPOSAL_UNMAPPED: 2}
    ranked.sort(key=lambda r: (order.get(r.status, 9), -r.score))
    best = ranked[0]

    top_exact = [
        r for r in ranked if r.status == PROPOSAL_EXACT and r.product_id is not None
    ]
    if len(top_exact) > 1:
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=best.product_id,
            product_code=best.product_code,
            score=best.score * 0.7,
            breakdown={
                "reason": "ambiguous_candidates",
                "candidates": [
                    {"product_id": r.product_id, "code": r.product_code}
                    for r in top_exact
                ],
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
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown=best.breakdown,
            reason=REASON_INSUFFICIENT_DATA,
        )

    return best
