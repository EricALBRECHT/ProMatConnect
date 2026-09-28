"""Extracteur FER_BETON — fer à béton torsadé (LINEAR_PROFILE)."""

from __future__ import annotations

import re

from app.models.product_mapping import (
    CATEGORY_FER_BETON,
    EXTRACTOR_VERSION_FER_BETON_V1,
    KIND_FER_BETON,
)
from app.services.product_mapping.linear_profile_shared import (
    ExtractionResult,
    diameter_profile,
    extract_bar_diameter_mm,
    extract_typed_linear,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

_EXCLUDE_RE = re.compile(r"tube|corniere|connecteur|\bkits?\b|assortiment", re.I)
_FER_RE = re.compile(r"fer\s+a\s+beton|fer\s+a\s+beton|beton\s+torsad", re.I)
# fold strips accents → "fer a beton"
_FER_FOLDED_RE = re.compile(r"fer\s+a\s+beton|torsad", re.I)


def is_fer_beton(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    if "fer a beton" in blob or ("torsad" in blob and "beton" in blob):
        return True
    return bool(re.search(r"fer\s+a\s+beton", blob))


def _profile(designation: str) -> str | None:
    d = extract_bar_diameter_mm(designation)
    if d is None:
        # « Ø 6 mm x L. 6 m » already covered; try bare « diamètre 10 mm »
        m = re.search(
            r"diametre\s*(?P<d>\d{1,2}(?:[.,]\d{1,2})?)\s*mm",
            _fold(designation),
            re.I,
        )
        if m:
            from app.services.product_mapping.primitives.textutil import parse_number

            parsed = parse_number(m.group("d"))
            if parsed is not None and 4 <= parsed <= 40:
                d = float(parsed)
    if d is None:
        return None
    return diameter_profile(d)


def extract_fer_beton(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    return extract_typed_linear(
        designation,
        kind=KIND_FER_BETON,
        category_code=CATEGORY_FER_BETON,
        extractor_version=EXTRACTOR_VERSION_FER_BETON_V1,
        classify=is_fer_beton,
        profile_fn=_profile,
    )
