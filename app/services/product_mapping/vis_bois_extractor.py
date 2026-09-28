"""Extracteur VIS_BOIS — type=bois + Ø×L (DIMENSIONAL_FASTENER)."""

from __future__ import annotations

import re

from app.models.product_mapping import (
    CATEGORY_VIS_BOIS,
    EXTRACTOR_VERSION_VIS_BOIS_V1,
)
from app.services.product_mapping.dimensional_fastener_shared import (
    ExtractionResult,
    extract_typed_fastener,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

TYPE_BOIS = "bois"

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
            r"tirefond",
            r"\bagglo\b",
            r"\bbeton\b",
            r"multi[\s-]?materiau",
            r"multi[\s-]?usage",
            r"tous[\s-]?materiau",
            r"universel",
            r"assortiment",
            r"\bkits?\b",
            r"anneau",
            r"patte",
            r"rallonge",
        )
    ),
    re.I,
)

_VIS_RE = re.compile(r"\bvis\b", re.I)
_BOIS_RE = re.compile(r"\bbois\b", re.I)


def is_vis_bois(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    return bool(_VIS_RE.search(blob) and _BOIS_RE.search(blob))


def extract_vis_bois(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    return extract_typed_fastener(
        designation,
        type_value=TYPE_BOIS,
        category_code=CATEGORY_VIS_BOIS,
        extractor_version=EXTRACTOR_VERSION_VIS_BOIS_V1,
        classify=is_vis_bois,
        max_length_mm=300,
    )
