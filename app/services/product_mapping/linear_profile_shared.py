"""Socle extracteur LINEAR_PROFILE hors ossature — cornières / ronds / tubes / fers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.services.product_mapping.identity_models import NOMINAL_LENGTH_SPEC
from app.services.product_mapping.primitives.length import extract_bar_length_mm
from app.services.product_mapping.primitives.textutil import (
    fold,
    parse_number,
    round_half_up,
)
from app.services.product_mapping.rules.base import apply_normalization_specs

REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"


@dataclass(frozen=True)
class ExtractionResult:
    category_code: str | None
    attributes: dict[str, Any]
    confidence: float
    extractor_version: str
    classified: bool
    reason: str | None = None


def extract_typed_linear(
    designation: str,
    *,
    kind: str,
    category_code: str,
    extractor_version: str,
    classify: Callable[[str], bool],
    profile_fn: Callable[[str], str | None],
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
    profile = profile_fn(designation)
    length_mm = extract_bar_length_mm(designation)
    attrs: dict[str, Any] = {
        "kind": kind,
        "profile": profile,
        "length_mm": length_mm,
    }
    apply_normalization_specs(attrs, (NOMINAL_LENGTH_SPEC,))
    confidence = 0.4 + (0.3 if profile else 0.0) + (0.3 if length_mm else 0.0)
    return ExtractionResult(
        category_code=category_code,
        attributes=attrs,
        confidence=confidence,
        extractor_version=extractor_version,
        classified=True,
        reason=None,
    )


_SECTION_EQ_RE = re.compile(
    r"(?<![\d.,])(?P<a>\d{1,3}(?:[.,]\d{1,2})?)\s*[x×*]\s*"
    r"(?P<b>\d{1,3}(?:[.,]\d{1,2})?)\s*mm",
    re.I,
)
_SECTION_LABELED_RE = re.compile(
    r"(?:section|h\.?)\s*(?P<a>\d{1,3}(?:[.,]\d{1,2})?)\s*[x×*]\s*"
    r"(?:l\.?\s*)?(?P<b>\d{1,3}(?:[.,]\d{1,2})?)\s*mm",
    re.I,
)
_DIAM_RE = re.compile(
    r"(?:o|ø|diam(?:etre)?\.?|diametre)\s*(?P<d>\d{1,2}(?:[.,]\d{1,2})?)\s*mm"
    r"|(?<![\d.,])(?P<d2>\d{1,2}(?:[.,]\d{1,2})?)\s*mm(?!\s*[x×])",
    re.I,
)
_TUBE_SECTION_RE = re.compile(
    r"(?<![\d.,])(?P<od>\d{1,2}(?:[.,]\d{1,2})?)\s*[x×*]\s*"
    r"(?P<wall>\d{1,2}(?:[.,]\d{1,2})?)\s*mm",
    re.I,
)


def _fmt_num(value: float) -> str:
    d = round(value, 2)
    if abs(d - int(d)) < 1e-9:
        return str(int(d))
    return str(d).replace(".", ",")


def extract_section_mm(text: str) -> tuple[float, float] | None:
    blob = fold(text).replace("×", "x")
    for pattern in (_SECTION_LABELED_RE, _SECTION_EQ_RE):
        m = pattern.search(blob)
        if not m:
            continue
        a = parse_number(m.group("a"))
        b = parse_number(m.group("b"))
        if a is None or b is None:
            continue
        if not (3 <= a <= 120 and 3 <= b <= 120):
            continue
        return float(a), float(b)
    # Habillage « l. 30 mm x H. 30 mm »
    m = re.search(
        r"l\.?\s*(?P<a>\d{1,3})\s*mm\s*[x×*]\s*h\.?\s*(?P<b>\d{1,3})\s*mm",
        blob,
        re.I,
    )
    if m:
        a = parse_number(m.group("a"))
        b = parse_number(m.group("b"))
        if a is not None and b is not None and 3 <= a <= 120 and 3 <= b <= 120:
            return float(a), float(b)
    return None


def extract_bar_diameter_mm(text: str) -> float | None:
    blob = fold(text).replace("ø", "o").replace("×", "x")
    m = re.search(
        r"(?:o\.?|diam(?:etre)?\.?)\s*(?P<d>\d{1,2}(?:[.,]\d{1,2})?)\s*mm",
        blob,
        re.I,
    )
    if m:
        d = parse_number(m.group("d"))
        if d is not None and 3 <= d <= 40:
            return float(d)
    # « Rond acier … 10 mm 1 m » — premier mm avant la longueur en m
    m = re.search(
        r"(?<![\d.,])(?P<d>\d{1,2}(?:[.,]\d{1,2})?)\s*mm\s+(?P<l>\d+(?:[.,]\d+)?)\s*m\b",
        blob,
        re.I,
    )
    if m:
        d = parse_number(m.group("d"))
        if d is not None and 3 <= d <= 40:
            return float(d)
    return None


def extract_tube_od_wall_mm(text: str) -> tuple[float, float] | None:
    blob = fold(text).replace("×", "x")
    m = _TUBE_SECTION_RE.search(blob)
    if not m:
        return None
    od = parse_number(m.group("od"))
    wall = parse_number(m.group("wall"))
    if od is None or wall is None:
        return None
    if not (8 <= od <= 80 and 0.5 <= wall <= 5):
        return None
    return float(od), float(wall)


def section_profile(a: float, b: float, color: str, *, adhesive: bool = False) -> str:
    left, right = sorted((a, b))
    # Conserver l'ordre d'annonce si inégal (10x20 ≠ 20x10 commercialement rare ;
    # on normalise croissant pour stabilité).
    base = f"{_fmt_num(left)}X{_fmt_num(right)}_{color}"
    if adhesive:
        base += "_ADH"
    return base


def diameter_profile(diameter_mm: float) -> str:
    return f"D{_fmt_num(diameter_mm)}"


def tube_profile(od: float, wall: float) -> str:
    return f"{_fmt_num(od)}X{_fmt_num(wall)}"
