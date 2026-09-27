"""Primitive « longueur de barre » — profilés, rails, montants, fourrures.

Bornes métier : 500 mm à 12 000 mm. Première valeur plausible rencontrée.
"""

from __future__ import annotations

import re

from app.services.product_mapping.primitives.textutil import parse_number, to_mm

MIN_BAR_MM = 500
MAX_BAR_MM = 12000

_LENGTH_PATTERNS = (
    # 48 x 2490 mm (souvent après profil)
    re.compile(
        r"\b\d{2}\s*[x×]\s*(?P<l>\d{3,5})\s*mm\b",
        re.I,
    ),
    # L. 3 m / L.2,50m / longueur 3 m
    re.compile(
        r"(?:l\.?|longueur)\s*(?P<l>\d+(?:[.,]\d+)?)\s*(?P<u>mm|cm|m|ml)\b",
        re.I,
    ),
    # 3ML / 2,50ML / 5,3 m / 3000 mm
    re.compile(
        r"(?<![A-Za-z0-9])(?P<l>\d+(?:[.,]\d+)?)\s*(?P<u>mm|cm|m|ml)\b",
        re.I,
    ),
    # « - 3 m NF » / « - 2,50 m »
    re.compile(
        r"[-–]\s*(?P<l>\d+(?:[.,]\d+)?)\s*(?P<u>m|ml|cm|mm)\b",
        re.I,
    ),
)


def extract_bar_length_mm(text: str) -> int | None:
    """Longueur barre en mm — None si aucune valeur plausible."""
    for pattern in _LENGTH_PATTERNS:
        for m in pattern.finditer(text.replace("×", "x")):
            n = parse_number(m.group("l"))
            if n is None:
                continue
            groups = m.groupdict()
            unit = groups.get("u")
            # Pattern « 48 x 2490 mm » : le groupe l est déjà exprimé en mm
            if unit is None:
                if MIN_BAR_MM <= int(n) <= MAX_BAR_MM:
                    return int(n)
                continue
            mm = to_mm(n, unit, min_mm=MIN_BAR_MM, max_mm=MAX_BAR_MM)
            if mm is not None:
                return mm
    return None
