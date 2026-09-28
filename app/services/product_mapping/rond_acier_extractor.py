"""Extracteur ROND_ACIER — barre pleine étirée (LINEAR_PROFILE)."""

from __future__ import annotations

import re

from app.models.product_mapping import (
    CATEGORY_ROND_ACIER,
    EXTRACTOR_VERSION_ROND_ACIER_V1,
    KIND_ROND_ACIER,
)
from app.services.product_mapping.linear_profile_shared import (
    ExtractionResult,
    diameter_profile,
    extract_bar_diameter_mm,
    extract_typed_linear,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

_EXCLUDE_RE = re.compile(
    r"tube|fer\s+a\s+beton|torsad|pvc|connecteur|tringle|chrome|aluminium|\balu\b",
    re.I,
)
_ROND_RE = re.compile(r"\brond\b", re.I)
_ACIER_RE = re.compile(r"\baci?er\b|\betire\b", re.I)


def is_rond_acier(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    return bool(_ROND_RE.search(blob) and _ACIER_RE.search(blob))


def _profile(designation: str) -> str | None:
    d = extract_bar_diameter_mm(designation)
    if d is None:
        return None
    return diameter_profile(d)


def extract_rond_acier(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    return extract_typed_linear(
        designation,
        kind=KIND_ROND_ACIER,
        category_code=CATEGORY_ROND_ACIER,
        extractor_version=EXTRACTOR_VERSION_ROND_ACIER_V1,
        classify=is_rond_acier,
        profile_fn=_profile,
    )
