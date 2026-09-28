"""Extracteur CORNIERE_PVC — LINEAR_PROFILE kind=CORNIERE_PVC."""

from __future__ import annotations

import re

from app.models.product_mapping import (
    CATEGORY_CORNIERE_PVC,
    EXTRACTOR_VERSION_CORNIERE_PVC_V1,
    KIND_CORNIERE_PVC,
)
from app.services.product_mapping.linear_profile_shared import (
    ExtractionResult,
    extract_section_mm,
    extract_typed_linear,
    section_profile,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

_EXCLUDE_RE = re.compile(
    "|".join(
        (
            r"assortiment",
            r"\blots?\b",
            r"\bkits?\b",
            r"meuble",
            r"cuisine",
            r"bardage",
            r"\bplaco\b",
            r"perfore",
            r"\bacier\b",
            r"inoxidable",
            r"inox",
            r"galvanis",
            r"\balu\b(?!\s)",  # « gris alu » OK via color; raw aluminium stock excluded elsewhere
        )
    ),
    re.I,
)

# « Cornière PVC acier inoxidable » = incohérent
_SUSPECT_RE = re.compile(r"acier\s+inoxidable|inox", re.I)

_CORNIERE_RE = re.compile(r"corni", re.I)
_PVC_RE = re.compile(r"\bpvc\b", re.I)

_COLOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("gris_alu", re.compile(r"gris\s+alu", re.I)),
    ("gris_titane", re.compile(r"gris\s+titane", re.I)),
    ("blanc", re.compile(r"blanc", re.I)),
    ("noir", re.compile(r"noir", re.I)),
    ("gris", re.compile(r"\bgris\b", re.I)),
)


def _color(blob: str) -> str | None:
    for label, pattern in _COLOR_PATTERNS:
        if pattern.search(blob):
            return label
    return None


def is_corniere_pvc(designation: str) -> bool:
    blob = _fold(designation)
    if not (_CORNIERE_RE.search(blob) and _PVC_RE.search(blob)):
        return False
    if _SUSPECT_RE.search(blob):
        return False
    if re.search(
        r"meuble|cuisine|bardage|assortiment|\blots?\b|\bkits?\b|perfore|"
        r"\bplaco\b|galvanis|\bacier\b",
        blob,
    ):
        return False
    return True


def _profile(designation: str) -> str | None:
    blob = _fold(designation)
    color = _color(blob)
    section = extract_section_mm(designation)
    if color is None or section is None:
        return None
    adhesive = bool(re.search(r"adhesiv", blob))
    return section_profile(section[0], section[1], color, adhesive=adhesive)


def extract_corniere_pvc(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    return extract_typed_linear(
        designation,
        kind=KIND_CORNIERE_PVC,
        category_code=CATEGORY_CORNIERE_PVC,
        extractor_version=EXTRACTOR_VERSION_CORNIERE_PVC_V1,
        classify=is_corniere_pvc,
        profile_fn=_profile,
    )
