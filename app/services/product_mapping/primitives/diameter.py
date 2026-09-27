"""Primitive « diamètre × longueur » — visserie, tiges, chevilles.

Formats couverts : « 3,5 x 25 mm », « 3,5X25 mm », « Ø 3,5 x 45 mm »,
« 4.8 x 120 mm », « 3,5 mm x 9,5 mm », « 8 × 35 ».

Bornes métier visserie : diamètre 2,0 à 8,0 mm, longueur 10 à 200 mm. Un couple
hors bornes est ignoré (on continue la lecture) — jamais corrigé ni inventé.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.product_mapping.primitives.textutil import (
    fold,
    parse_number,
    round_half_up,
)

MIN_DIAMETER_MM = Decimal("2.0")
MAX_DIAMETER_MM = Decimal("8.0")
MIN_SCREW_LENGTH_MM = 10
MAX_SCREW_LENGTH_MM = 200

# Ø / diam. optionnels, unité mm optionnelle de chaque côté du séparateur.
# Les gardes (?<![\d.,]) / (?![\d]) interdisent de lire un couple à l'intérieur
# d'un nombre plus long (« BTE1500x » ne donne pas 15 × 00).
_DIAMETER_LENGTH_RE = re.compile(
    r"(?:(?:o|diam(?:etre)?)\s*\.?\s*)?"
    r"(?<![\d.,])(?P<d>\d{1,2}(?:[.,]\d{1,2})?)\s*(?:mm)?\s*"
    r"[x*]\s*"
    r"(?P<l>\d{1,3}(?:[.,]\d{1,2})?)(?!\d)\s*(?:mm)?",
    re.I,
)


def extract_diameter_length_mm(text: str) -> tuple[float | None, int | None]:
    """(diamètre mm, longueur mm) — (None, None) si aucun couple plausible.

    Le diamètre est rendu en mm réels (3.5, pas 35 dixièmes) pour coller aux
    attributs PMC ; la longueur en mm entiers.
    """
    # « Ø » ne se décompose pas en NFKD : on l'aligne sur le « o » du motif.
    blob = fold(text).replace("×", "x").replace("ø", "o")
    for m in _DIAMETER_LENGTH_RE.finditer(blob):
        diameter = parse_number(m.group("d"))
        length = parse_number(m.group("l"))
        if diameter is None or length is None:
            continue
        if not MIN_DIAMETER_MM <= diameter <= MAX_DIAMETER_MM:
            continue
        length_mm = round_half_up(length)
        if not MIN_SCREW_LENGTH_MM <= length_mm <= MAX_SCREW_LENGTH_MM:
            continue
        return float(diameter), length_mm
    return None, None
