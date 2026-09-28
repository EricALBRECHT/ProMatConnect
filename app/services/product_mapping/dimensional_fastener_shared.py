"""Socle extracteur DIMENSIONAL_FASTENER — partagé VIS_BOIS / VIS_MULTI / CHEVILLE.

Pas de matcher dédié : classification légère + Ø×L + attributs optionnels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from app.services.product_mapping.primitives.diameter import (
    MAX_DIAMETER_MM,
    MIN_DIAMETER_MM,
    MIN_SCREW_LENGTH_MM,
    extract_diameter_length_mm,
)
from app.services.product_mapping.primitives.packaging import extract_piece_count
from app.services.product_mapping.primitives.textutil import (
    fold,
    parse_number,
    round_half_up,
)

REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"

_PAIR_RE = re.compile(
    r"(?:(?:o|diam(?:etre)?)\s*\.?\s*)?"
    r"(?<![\d.,])(?P<d>\d{1,2}(?:[.,]\d{1,2})?)\s*(?:mm)?\s*"
    r"[x*]\s*"
    r"(?P<l>\d{1,3}(?:[.,]\d{1,2})?)(?!\d)\s*(?:mm)?",
    re.I,
)

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


def _first_match(blob: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    for label, pattern in patterns:
        if pattern.search(blob):
            return label
    return None


def optional_secondary(blob: str) -> dict[str, Any]:
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


def extract_pair_mm(
    text: str,
    *,
    max_diameter_mm: Decimal = MAX_DIAMETER_MM,
    max_length_mm: int = 300,
) -> tuple[float | None, int | None]:
    """Ø×L avec bornes configurables (chevilles > 8 mm possibles)."""
    diameter_mm, length_mm = extract_diameter_length_mm(text)
    if diameter_mm is not None and length_mm is not None:
        if Decimal(str(diameter_mm)) <= max_diameter_mm and length_mm <= max_length_mm:
            return diameter_mm, length_mm
    blob = fold(text).replace("×", "x").replace("ø", "o")
    for match in _PAIR_RE.finditer(blob):
        diameter = parse_number(match.group("d"))
        length = parse_number(match.group("l"))
        if diameter is None or length is None:
            continue
        if not MIN_DIAMETER_MM <= diameter <= max_diameter_mm:
            continue
        length_i = round_half_up(length)
        if not MIN_SCREW_LENGTH_MM <= length_i <= max_length_mm:
            continue
        return float(diameter), length_i
    return None, None


def extract_typed_fastener(
    designation: str,
    *,
    type_value: str,
    category_code: str,
    extractor_version: str,
    classify: Callable[[str], bool],
    max_diameter_mm: Decimal = MAX_DIAMETER_MM,
    max_length_mm: int = 300,
) -> ExtractionResult:
    if not classify(designation):
        return ExtractionResult(
            category_code=None,
            attributes={},
            confidence=0.0,
            extractor_version=extractor_version,
            classified=False,
            reason=REASON_NOT_THIS_CATEGORY,
        )
    blob = fold(designation)
    diameter_mm, length_mm = extract_pair_mm(
        designation, max_diameter_mm=max_diameter_mm, max_length_mm=max_length_mm
    )
    attrs: dict[str, Any] = {
        "type": type_value,
        "diameter_mm": diameter_mm,
        "length_mm": length_mm,
        "packaging_qty": extract_piece_count(designation),
        **optional_secondary(blob),
    }
    confidence = 0.4 + (0.3 if diameter_mm is not None else 0.0) + (
        0.3 if length_mm is not None else 0.0
    )
    return ExtractionResult(
        category_code=category_code,
        attributes=attrs,
        confidence=confidence,
        extractor_version=extractor_version,
        classified=True,
        reason=None,
    )
