"""Matching OSSATURE_PLACO — façade sur le matcher générique V2.

length_mm fournisseur conservé tel quel ; matching via nominal_length_mm contre
Product.attributes.length_mm. Table nominale explicite — pas de tolérance ±.
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
from app.services.product_mapping.rules.ossature_placo import (
    IDENTITY_KEYS as EXTRACTED_IDENTITY,
)
from app.services.product_mapping.rules.ossature_placo import (
    OSSATURE_PLACO_RULE,
    kind_from_product,
)

# extracted.nominal_length_mm == product.length_mm
PRODUCT_IDENTITY = ("kind", "profile", "length_mm")

__all__ = [
    "EXTRACTED_IDENTITY",
    "MatchResult",
    "PRODUCT_IDENTITY",
    "best_match",
    "kind_from_product",
    "score_against_product",
]


def score_against_product(
    extracted: dict[str, Any],
    *,
    product_id: int,
    product_code: str,
    product_attrs: dict | None,
    subcategory: str | None = None,
) -> MatchResult:
    return _generic_score(
        OSSATURE_PLACO_RULE,
        extracted,
        product_id=product_id,
        product_code=product_code,
        product_attrs=product_attrs,
        subcategory=subcategory,
    )


def best_match(
    extracted: dict[str, Any],
    candidates: Sequence[Sequence],
) -> MatchResult:
    return _generic_best_match(OSSATURE_PLACO_RULE, extracted, candidates)
