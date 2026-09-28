"""Extracteur CHEVILLE_METAL — type=cheville_metal + Ø×L corps de cheville."""

from __future__ import annotations

import re
from decimal import Decimal

from app.models.product_mapping import (
    CATEGORY_CHEVILLE_METAL,
    EXTRACTOR_VERSION_CHEVILLE_METAL_V1,
)
from app.services.product_mapping.dimensional_fastener_shared import (
    ExtractionResult,
    extract_typed_fastener,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

TYPE_CHEVILLE_METAL = "cheville_metal"

_EXCLUDE_RE = re.compile(
    "|".join(
        (
            r"nylon",
            r"plastique",
            r"plastic",
            r"\bvis\b",  # packs « cheville + vis » → hors identité unitaire
            r"avec\s+vis",
            r"pattes?\s+a\s+vis",
            r"assortiment",
            r"\bkits?\b",
            r"coffret",
        )
    ),
    re.I,
)

_CHEVILLE_RE = re.compile(r"chevil", re.I)
_METAL_RE = re.compile(
    r"metal|metall|acier|laiton|molly|expansion|a\s+frappe|autoforeuse",
    re.I,
)


def is_cheville_metal(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    return bool(_CHEVILLE_RE.search(blob) and _METAL_RE.search(blob))


def extract_cheville_metal(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    # Chevilles : Ø souvent > 8 mm (10–14).
    return extract_typed_fastener(
        designation,
        type_value=TYPE_CHEVILLE_METAL,
        category_code=CATEGORY_CHEVILLE_METAL,
        extractor_version=EXTRACTOR_VERSION_CHEVILLE_METAL_V1,
        classify=is_cheville_metal,
        max_diameter_mm=Decimal("20"),
        max_length_mm=200,
    )
