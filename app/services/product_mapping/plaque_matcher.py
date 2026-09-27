"""Matching PLAQUE_PLATRE — façade sur le matcher générique V2.

La logique vit dans generic_matcher, pilotée par PLAQUE_PLATRE_RULE. Ce module
conserve les signatures historiques de la catégorie.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.services.product_mapping.generic_matcher import (
    MatchResult,
)
from app.services.product_mapping.generic_matcher import (
    best_match as _generic_best_match,
)
from app.services.product_mapping.generic_matcher import (
    score_against_product as _generic_score,
)
from app.services.product_mapping.rules.plaque_platre import (
    BOOL_COMPAT_KEYS,
    IDENTITY_KEYS,
    PLAQUE_PLATRE_RULE,
)

__all__ = [
    "BOOL_COMPAT_KEYS",
    "IDENTITY_KEYS",
    "MatchResult",
    "best_match",
    "score_against_product",
]


def score_against_product(
    extracted: dict[str, Any],
    product_attrs: dict | None,
    *,
    product_id: int,
    product_code: str,
) -> MatchResult:
    return _generic_score(
        PLAQUE_PLATRE_RULE,
        extracted,
        product_id=product_id,
        product_code=product_code,
        product_attrs=product_attrs,
    )


def best_match(
    extracted: dict[str, Any],
    candidates: Sequence[Sequence],
) -> MatchResult:
    return _generic_best_match(PLAQUE_PLATRE_RULE, extracted, candidates)
