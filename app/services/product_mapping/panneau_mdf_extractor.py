"""Extracteur PANNEAU_MDF — BOARD_PANEL type=mdf."""

from __future__ import annotations

import re
from typing import Any

from app.models.product_mapping import (
    CATEGORY_PANNEAU_MDF,
    EXTRACTOR_VERSION_PANNEAU_MDF_V1,
)
from app.services.product_mapping.primitives.dimensions import (
    extract_dimensions_mm,
    extract_length_width_mm,
    extract_thickness_alone_mm,
)
from app.services.product_mapping.primitives.textutil import fold as _fold

REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"
TYPE_MDF = "mdf"

_EXCLUDE_RE = re.compile(
    "|".join(
        (
            r"equerre",
            r"\blots?\b",
            r"\bkits?\b",
            r"porte",
            r"cloison",
            r"treillis",
            r"brise[\s-]?vent",
            r"cloture",
            r"plinthe",
            r"lambris",
            r"chant\b",
            r"meuble",
            r"ilot",
        )
    ),
    re.I,
)

_MDF_RE = re.compile(r"\bmdf\b", re.I)
_PANNEAU_RE = re.compile(r"\bpanneau\b", re.I)

# Formats sûrs observés (mm) — longueur × largeur canoniques.
_ALLOWED_FORMATS = frozenset(
    {
        (1220, 610),
        (810, 405),
    }
)
_ALLOWED_THICKNESS = frozenset({6, 9, 12, 18})


class ExtractionResult:
    __slots__ = (
        "category_code",
        "attributes",
        "confidence",
        "extractor_version",
        "classified",
        "reason",
    )

    def __init__(
        self,
        *,
        category_code: str | None,
        attributes: dict[str, Any],
        confidence: float,
        extractor_version: str,
        classified: bool,
        reason: str | None = None,
    ):
        self.category_code = category_code
        self.attributes = attributes
        self.confidence = confidence
        self.extractor_version = extractor_version
        self.classified = classified
        self.reason = reason


def is_panneau_mdf(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    if not _MDF_RE.search(blob):
        return False
    # Exiger « panneau » pour éviter chants/baguettes MDF
    return bool(_PANNEAU_RE.search(blob))


def extract_panneau_mdf(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    if not is_panneau_mdf(designation):
        return ExtractionResult(
            category_code=None,
            attributes={},
            confidence=0.0,
            extractor_version=EXTRACTOR_VERSION_PANNEAU_MDF_V1,
            classified=False,
            reason=REASON_NOT_THIS_CATEGORY,
        )
    # « 1 220 x 610 » → retirer espaces milliers
    cleaned = re.sub(r"(\d)\s+(\d{3})\b", r"\1\2", designation)
    length_mm, width_mm, thickness_mm = extract_dimensions_mm(cleaned)
    if length_mm is None and width_mm is None and thickness_mm is None:
        length_mm, width_mm = extract_length_width_mm(cleaned)
        thickness_mm = extract_thickness_alone_mm(cleaned)
    # Formats restreints : fiabilité > couverture (8 formats + grands 2440×1220 MDF)
    if (
        length_mm is not None
        and width_mm is not None
        and (length_mm, width_mm) not in _ALLOWED_FORMATS
        and (width_mm, length_mm) not in _ALLOWED_FORMATS
    ):
        length_mm, width_mm, thickness_mm = None, None, None
    if thickness_mm is not None and thickness_mm not in _ALLOWED_THICKNESS:
        thickness_mm = None
    # Canoniser L ≥ l
    if length_mm is not None and width_mm is not None and width_mm > length_mm:
        length_mm, width_mm = width_mm, length_mm
    attrs = {
        "type": TYPE_MDF,
        "length_mm": length_mm,
        "width_mm": width_mm,
        "thickness_mm": thickness_mm,
    }
    conf = 0.4 + 0.2 * sum(
        1 for k in ("length_mm", "width_mm", "thickness_mm") if attrs[k] is not None
    )
    return ExtractionResult(
        category_code=CATEGORY_PANNEAU_MDF,
        attributes=attrs,
        confidence=conf,
        extractor_version=EXTRACTOR_VERSION_PANNEAU_MDF_V1,
        classified=True,
        reason=None,
    )
