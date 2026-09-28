"""Extracteur VIS_MULTI — type=multi + Ø×L (multi-matériaux / tous matériaux)."""

from __future__ import annotations

import re

from app.models.product_mapping import (
    CATEGORY_VIS_MULTI,
    EXTRACTOR_VERSION_VIS_MULTI_V1,
)
from app.services.product_mapping.dimensional_fastener_shared import (
    ExtractionResult,
    extract_typed_fastener,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

TYPE_MULTI = "multi"

_EXCLUDE_RE = re.compile(
    "|".join(
        (
            r"visseuse",
            r"embout",
            r"plaq(?:ues?)?\.?\s*(?:de\s+)?pl?atre",
            r"\bplaco\b",
            r"trompette",
            r"\bttpc\b",
            r"cheville",
            r"boulon",
            r"\bagglo\b",
            r"assortiment",
            r"\bkits?\b",
            r"ni\s+clou\s+ni\s+vis",
            r"\bcolle\b",
            r"\bcollage\b",
            r"\bmastic\b",
            r"\badhesif\b",
        )
    ),
    re.I,
)

_VIS_RE = re.compile(r"\bvis\b", re.I)
# Famille distincte de VIS_BOIS : multi-matériaux / tous matériaux / multi-supports.
_MULTI_RE = re.compile(
    r"multi[\s-]?materiau|tous[\s-]?materiau|multi[\s-]?support|multi[\s-]?usage",
    re.I,
)


def is_vis_multi(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    return bool(_VIS_RE.search(blob) and _MULTI_RE.search(blob))


def extract_vis_multi(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    return extract_typed_fastener(
        designation,
        type_value=TYPE_MULTI,
        category_code=CATEGORY_VIS_MULTI,
        extractor_version=EXTRACTOR_VERSION_VIS_MULTI_V1,
        classify=is_vis_multi,
        max_length_mm=300,
    )
