"""Primitives dimensionnelles L × l × Ép. — partagées entre catégories planes.

Comportements conservés depuis PLAQUE_PLATRE V1.3 :
- unité partagée quand une seule est présente sur le couple L × l ;
- permutation si largeur > longueur ;
- rejet des doublages « Ép. 10 + 40 mm » ;
- épaisseur nue < 100 interprétée en mm.
"""

from __future__ import annotations

import re

from app.services.product_mapping.primitives.textutil import (
    parse_number,
    round_half_up,
    to_mm,
)

MIN_MM = 1
MAX_MM = 10000

_DIM_TRIPLE = re.compile(
    r"(?:l\.?\s*)?(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]\s*"
    r"(?:l\.?\s*)?(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?\s*[x×*]\s*"
    r"(?:epaisseur|épaisseur|ep\.?\s*|ép\.?\s*|e\.?\s*)?(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
    re.I,
)
_DIM_LABELED = re.compile(
    r"l\.?\s*(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]?\s*"
    r"l\.?\s*(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?\s*[x×*]?\s*"
    r"(?:epaisseur|épaisseur|ep\.?|ép\.?|e\.?)\s*(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
    re.I,
)

# L × l sans épaisseur (labels optionnels) — ex. « 2,50 x 1,20 m », « 2,5 X 1,2M »
_DIM_PAIR = re.compile(
    r"(?:l\.?\s*)?(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]\s*"
    r"(?:l\.?\s*)?(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?",
    re.I,
)

# Épaisseur seule — ex. « ép. 13 mm », « épaisseur 1,25 cm »
_THICKNESS_ONLY = re.compile(
    r"(?:epaisseur|épaisseur|ép\.?|ep\.?)\s*(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
    re.I,
)


def _dim_to_mm(value, unit: str | None) -> int | None:
    return to_mm(
        value,
        unit,
        min_mm=MIN_MM,
        max_mm=MAX_MM,
        bare_small_as_meters=True,
    )


def _apply_shared_unit(ul: str | None, uw: str | None) -> tuple[str | None, str | None]:
    """Si une seule unité est présente sur L×l, l'appliquer aux deux."""
    if ul and not uw:
        return ul, ul
    if uw and not ul:
        return uw, uw
    return ul, uw


def _thickness_to_mm(raw_value, unit: str | None) -> int | None:
    if unit:
        return _dim_to_mm(raw_value, unit)
    if raw_value < 100:
        return round_half_up(raw_value)
    return _dim_to_mm(raw_value, "mm")


def _pair_to_mm(
    l_raw: str, w_raw: str, ul: str | None, uw: str | None
) -> tuple[int | None, int | None]:
    length_raw = parse_number(l_raw)
    width_raw = parse_number(w_raw)
    if length_raw is None or width_raw is None:
        return None, None
    ul, uw = _apply_shared_unit(ul, uw)
    length = _dim_to_mm(length_raw, ul)
    width = _dim_to_mm(width_raw, uw)
    if length and width:
        if width > length:
            length, width = width, length
        return length, width
    return None, None


def extract_dimensions_mm(text: str) -> tuple[int | None, int | None, int | None]:
    """L × l × Ép. en mm — (None, None, None) si le triplet n'est pas lisible."""
    normalized = text.replace("×", "x")
    for pattern in (_DIM_LABELED, _DIM_TRIPLE):
        m = pattern.search(normalized)
        if not m:
            continue
        # Rejeter « 13 + 80 » (doublage) — le groupe t ne doit pas être suivi de +
        t_span = m.end("t")
        if t_span < len(normalized) and normalized[t_span : t_span + 2].lstrip().startswith(
            "+"
        ):
            continue
        length_raw = parse_number(m.group("l"))
        width_raw = parse_number(m.group("w"))
        t = parse_number(m.group("t"))
        if length_raw is None or width_raw is None or t is None:
            continue
        ul, uw = _apply_shared_unit(m.group("ul"), m.group("uw"))
        length = _dim_to_mm(length_raw, ul)
        width = _dim_to_mm(width_raw, uw)
        thickness = _thickness_to_mm(t, m.group("ut"))
        if length and width and thickness:
            if width > length:
                length, width = width, length
            return length, width, thickness
    return None, None, None


def extract_length_width_mm(text: str) -> tuple[int | None, int | None]:
    """L × l sans épaisseur — labels optionnels (« 2,50 x 1,20 m », « 120x250 cm »)."""
    m = _DIM_PAIR.search(text.replace("×", "x"))
    if not m:
        return None, None
    return _pair_to_mm(m.group("l"), m.group("w"), m.group("ul"), m.group("uw"))


def extract_thickness_alone_mm(text: str) -> int | None:
    """Épaisseur isolée — ex. « ép. 13 mm », « épaisseur 1,25 cm »."""
    m = _THICKNESS_ONLY.search(text.replace("×", "x"))
    if not m:
        return None
    t = parse_number(m.group("t"))
    if t is None:
        return None
    return _thickness_to_mm(t, m.group("ut"))
