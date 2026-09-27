"""Matching PLAQUE_PLATRE — caractéristiques techniques prioritaires.

UNKNOWN (null) ≠ false : une info absente ne crée pas d'EXACT artificiel.
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

IDENTITY_KEYS = ("length_mm", "width_mm", "thickness_mm", "type")
BOOL_COMPAT_KEYS = ("hydrofuge", "fire_resistant", "acoustic")


@dataclass(frozen=True)
class MatchResult:
    status: str
    product_id: int | None
    product_code: str | None
    score: float
    breakdown: dict[str, Any]
    algorithm_version: str = ALGORITHM_VERSION_PLAQUE_V1


def _norm_product_attrs(raw: dict | None) -> dict[str, Any]:
    """Normalise Product.attributes seed → schéma extracteur."""
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
    # Dériver flags UNIQUEMENT quand le type l'implique explicitement
    if ptype == "hydrofuge":
        out["hydrofuge"] = True
    elif ptype == "feu":
        out["fire_resistant"] = True
    elif ptype == "phonique":
        out["acoustic"] = True
    # standard / legere / multifonctions : flags restent None (unknown)
    # sauf si explicitement présents dans attributes
    for key in BOOL_COMPAT_KEYS:
        if key in attrs and attrs[key] is not None:
            out[key] = bool(attrs[key])
    return out


def _bool_conflict(a: Any, b: Any) -> bool:
    """True si les deux côtés sont bool connus et différents."""
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

    # Conflits de flags connus
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
            breakdown["identity_mismatch"].append({"key": key, "extracted": ev, "product": tv})
        else:
            breakdown["identity_ok"].append(key)

    # Dimensions seules (sans type)
    dims_ok = all(
        extracted.get(k) is not None
        and target.get(k) is not None
        and extracted.get(k) == target.get(k)
        for k in ("length_mm", "width_mm", "thickness_mm")
    )

    if breakdown["flag_conflicts"]:
        return MatchResult(
            status=PROPOSAL_UNMAPPED
            if not dims_ok
            else PROPOSAL_REVIEW,
            product_id=product_id if dims_ok else None,
            product_code=product_code if dims_ok else None,
            score=0.2 if dims_ok else 0.0,
            breakdown=breakdown,
        )

    if identity_complete and identity_match and not breakdown["flag_conflicts"]:
        return MatchResult(
            status=PROPOSAL_EXACT,
            product_id=product_id,
            product_code=product_code,
            score=1.0,
            breakdown=breakdown,
        )

    if dims_ok and extracted.get("type") and target.get("type"):
        if extracted["type"] == target["type"]:
            # type + dims OK mais identity_incomplete à cause d'un autre champ ?
            return MatchResult(
                status=PROPOSAL_HIGH,
                product_id=product_id,
                product_code=product_code,
                score=0.85,
                breakdown=breakdown,
            )
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=product_id,
            product_code=product_code,
            score=0.45,
            breakdown=breakdown,
        )

    if dims_ok:
        # dimensions OK, type manquant d'un côté → REVIEW (pas EXACT)
        return MatchResult(
            status=PROPOSAL_REVIEW,
            product_id=product_id,
            product_code=product_code,
            score=0.55,
            breakdown=breakdown,
        )

    return MatchResult(
        status=PROPOSAL_UNMAPPED,
        product_id=None,
        product_code=None,
        score=0.0,
        breakdown=breakdown,
    )


def best_match(
    extracted: dict[str, Any],
    candidates: list[tuple[int, str, dict | None]],
) -> MatchResult:
    """Choisit le meilleur candidat parmi Products PLAQUE_PLATRE."""
    if not extracted or not any(
        extracted.get(k) for k in ("length_mm", "width_mm", "thickness_mm", "type")
    ):
        return MatchResult(
            status=PROPOSAL_UNMAPPED,
            product_id=None,
            product_code=None,
            score=0.0,
            breakdown={"reason": "insufficient_extracted_attrs"},
        )

    ranked: list[MatchResult] = []
    for pid, code, attrs in candidates:
        ranked.append(
            score_against_product(extracted, attrs, product_id=pid, product_code=code)
        )

    order = {
        PROPOSAL_EXACT: 0,
        PROPOSAL_HIGH: 1,
        PROPOSAL_REVIEW: 2,
        PROPOSAL_UNMAPPED: 3,
    }
    ranked.sort(key=lambda r: (order.get(r.status, 9), -r.score))
    best = ranked[0]
    # Plusieurs EXACT/HIGH → REVIEW ambigu
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
                    {"product_id": r.product_id, "code": r.product_code, "score": r.score}
                    for r in top
                ],
                "primary": best.breakdown,
            },
        )
    return best
