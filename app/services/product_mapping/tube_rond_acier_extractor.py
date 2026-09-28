"""Extracteur TUBE_ROND_ACIER — tube profilé AF (LINEAR_PROFILE)."""

from __future__ import annotations

import re

from app.models.product_mapping import (
    CATEGORY_TUBE_ROND_ACIER,
    EXTRACTOR_VERSION_TUBE_ROND_ACIER_V1,
    KIND_TUBE_ROND_ACIER,
)
from app.services.product_mapping.linear_profile_shared import (
    ExtractionResult,
    extract_tube_od_wall_mm,
    extract_typed_linear,
    tube_profile,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

_EXCLUDE_RE = re.compile(
    r"connecteur|pvc|aluminium|\balu\b|chrome|tringle|blanc|decor",
    re.I,
)
_TUBE_RE = re.compile(r"\btube\b", re.I)
_ROND_RE = re.compile(r"\brond\b", re.I)
_ACIER_RE = re.compile(r"\baci?er\b", re.I)


def is_tube_rond_acier(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    return bool(_TUBE_RE.search(blob) and _ROND_RE.search(blob) and _ACIER_RE.search(blob))


def _profile(designation: str) -> str | None:
    pair = extract_tube_od_wall_mm(designation)
    if pair is None:
        return None
    return tube_profile(pair[0], pair[1])


def extract_tube_rond_acier(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    return extract_typed_linear(
        designation,
        kind=KIND_TUBE_ROND_ACIER,
        category_code=CATEGORY_TUBE_ROND_ACIER,
        extractor_version=EXTRACTOR_VERSION_TUBE_ROND_ACIER_V1,
        classify=is_tube_rond_acier,
        profile_fn=_profile,
    )
