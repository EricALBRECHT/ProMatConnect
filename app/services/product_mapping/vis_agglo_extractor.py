"""Extracteur / classifieur déterministe VIS_AGGLO V1.

Identité = type « agglo » + diamètre + longueur. Tête, empreinte et finition
sont des attributs optionnels (rapport / enrichissement), jamais identité.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.product_mapping import (
    CATEGORY_VIS_AGGLO,
    EXTRACTOR_VERSION_VIS_AGGLO_V1,
)
from app.services.product_mapping.primitives.diameter import (
    MAX_DIAMETER_MM,
    MIN_DIAMETER_MM,
    MIN_SCREW_LENGTH_MM,
    extract_diameter_length_mm,
)
from app.services.product_mapping.primitives.textutil import parse_number, round_half_up
from app.services.product_mapping.primitives.packaging import extract_piece_count
from app.services.product_mapping.primitives.textutil import fold as _fold, fold

_AGGLO_DIAMETER_LENGTH_RE = re.compile(
    r"(?:(?:o|diam(?:etre)?)\s*\.?\s*)?"
    r"(?<![\d.,])(?P<d>\d{1,2}(?:[.,]\d{1,2})?)\s*(?:mm)?\s*"
    r"[x*]\s*"
    r"(?P<l>\d{1,3}(?:[.,]\d{1,2})?)(?!\d)\s*(?:mm)?",
    re.I,
)

__all__ = [
    "ExtractionResult",
    "TYPE_AGGLO",
    "extract_vis_agglo",
    "is_excluded",
    "is_vis_agglo",
]

TYPE_AGGLO = "agglo"
REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"

_EXCLUDE_RE = re.compile(
    "|".join(
        (
            r"visseuse",
            r"embout",
            r"plaq(?:ues?)?\.?\s*(?:de\s+)?pl?atre",
            r"\bplaco\b",
            r"trompette",
            r"\bttpc\b",
            r"terrasse",
            r"beton",
            r"cheville",
            r"boulon",
            r"tirefond",
            r"\btole\b",
            r"assortiment",
            r"\bkits?\b",
        )
    ),
    re.I,
)

_MULTI_USAGE_RE = re.compile(r"multi[\s-]?usage", re.I)

_VIS_RE = re.compile(r"\bvis\b", re.I)
_AGGLO_RE = re.compile(r"\bagglo\b", re.I)

_HEAD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fraisee", re.compile(r"frais", re.I)),
    ("plate", re.compile(r"\bplates?\b", re.I)),
    ("turbo", re.compile(r"\bturbo\b", re.I)),
    ("hex", re.compile(r"\bhex(?:agonale?s?)?\b", re.I)),
)

_DRIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pozidriv", re.compile(r"pozidriv|posidriv", re.I)),
    ("philips", re.compile(r"philips", re.I)),
    ("torx", re.compile(r"\btorx\b|\btx\b", re.I)),
)

# Vis bois / agglo : longueurs au-delà de 200 mm (220, 260…) — hors placo.
_MAX_AGGLO_LENGTH_MM = 300

_FINISH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("zingue", re.compile(r"zingue", re.I)),
    ("jaune", re.compile(r"\bjaune\b", re.I)),
    ("inox", re.compile(r"\binox\b", re.I)),
)


@dataclass(frozen=True)
class ExtractionResult:
    category_code: str | None
    attributes: dict[str, Any]
    confidence: float
    extractor_version: str
    classified: bool
    reason: str | None = None


def is_excluded(designation: str) -> bool:
    blob = _fold(designation)
    if _MULTI_USAGE_RE.search(blob) and not _AGGLO_RE.search(blob):
        return True
    return bool(_EXCLUDE_RE.search(blob))


def is_vis_agglo(designation: str) -> bool:
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    return bool(_VIS_RE.search(blob) and _AGGLO_RE.search(blob))


def _first_match(blob: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    for label, pattern in patterns:
        if pattern.search(blob):
            return label
    return None


def _extract_agglo_diameter_length_mm(text: str) -> tuple[float | None, int | None]:
    """Même lecture que la primitive visserie, longueur max étendue (vis bois)."""
    diameter_mm, length_mm = extract_diameter_length_mm(text)
    if diameter_mm is not None and length_mm is not None:
        return diameter_mm, length_mm
    blob = fold(text).replace("×", "x").replace("ø", "o")
    for match in _AGGLO_DIAMETER_LENGTH_RE.finditer(blob):
        diameter = parse_number(match.group("d"))
        length = parse_number(match.group("l"))
        if diameter is None or length is None:
            continue
        if not MIN_DIAMETER_MM <= diameter <= MAX_DIAMETER_MM:
            continue
        length_mm = round_half_up(length)
        if not MIN_SCREW_LENGTH_MM <= length_mm <= _MAX_AGGLO_LENGTH_MM:
            continue
        return float(diameter), length_mm
    return None, None


def _optional_secondary(blob: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    head = _first_match(blob, _HEAD_PATTERNS)
    if head:
        out["head"] = head
    drive = _first_match(blob, _DRIVE_PATTERNS)
    if drive:
        out["drive"] = drive
    finish = _first_match(blob, _FINISH_PATTERNS)
    if finish:
        out["finish"] = finish
    return out


def _rejected() -> ExtractionResult:
    return ExtractionResult(
        category_code=None,
        attributes={},
        confidence=0.0,
        extractor_version=EXTRACTOR_VERSION_VIS_AGGLO_V1,
        classified=False,
        reason=REASON_NOT_THIS_CATEGORY,
    )


def extract_vis_agglo(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    if not is_vis_agglo(designation):
        return _rejected()

    blob = _fold(designation)
    diameter_mm, length_mm = _extract_agglo_diameter_length_mm(designation)
    attrs: dict[str, Any] = {
        "type": TYPE_AGGLO,
        "diameter_mm": diameter_mm,
        "length_mm": length_mm,
        "packaging_qty": extract_piece_count(designation),
        **_optional_secondary(blob),
    }
    confidence = 0.4 + (0.3 if diameter_mm is not None else 0.0) + (
        0.3 if length_mm is not None else 0.0
    )
    return ExtractionResult(
        category_code=CATEGORY_VIS_AGGLO,
        attributes=attrs,
        confidence=confidence,
        extractor_version=EXTRACTOR_VERSION_VIS_AGGLO_V1,
        classified=True,
        reason=None,
    )
