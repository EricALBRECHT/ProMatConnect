"""Primitives texte / nombres / unités — socle commun à toutes les catégories.

Aucune connaissance métier : normalisation de chaînes et conversions d'unités.
"""

from __future__ import annotations

import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_METER_UNITS = {"m", "ml", "metre", "mètre", "metres", "mètres"}
_BARE_METER_THRESHOLD = Decimal("20")


def fold(text: str) -> str:
    """NFKD + suppression des diacritiques + minuscules."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def parse_number(raw: str) -> Decimal | None:
    """« 2,50 » / « 2 500 » → Decimal ; None si non numérique."""
    try:
        return Decimal(raw.strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        return None


def to_mm(
    value: Decimal,
    unit: str | None,
    *,
    min_mm: int = 1,
    max_mm: int = 10000,
    bare_small_as_meters: bool = False,
) -> int | None:
    """Conversion en millimètres, arrondi half-up, bornée à [min_mm, max_mm].

    Unité absente : interprétée en mm, sauf si ``bare_small_as_meters`` et
    valeur < 20 (« 2,50 x 1,20 » → mètres). Unité inconnue → None.
    """
    u = (unit or "").lower().replace(" ", "")
    if u in _METER_UNITS:
        mm = value * 1000
    elif u == "cm":
        mm = value * 10
    elif u == "mm":
        mm = value
    elif u == "":
        if bare_small_as_meters and value < _BARE_METER_THRESHOLD:
            mm = value * 1000
        else:
            mm = value
    else:
        return None
    mm_i = int(mm.to_integral_value(rounding=ROUND_HALF_UP))
    if mm_i < min_mm or mm_i > max_mm:
        return None
    return mm_i


def round_half_up(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))
