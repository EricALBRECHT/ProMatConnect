"""Primitive conditionnement — « Lot de 10 », « Pack de 5 », « Paquet de 2 ».

Non utilisée par l'identité de matching des catégories actuelles ; disponible
pour les catégories vendues au lot.
"""

from __future__ import annotations

import re

from app.services.product_mapping.primitives.textutil import fold

_LOT_RE = re.compile(
    r"\b(?:lot|pack|paquet|botte|colis)\s*(?:de\s*)?(?P<n>\d{1,4})\b",
    re.I,
)


def extract_lot_quantity(text: str) -> int | None:
    """Nombre de pièces par lot — None si non exprimé."""
    m = _LOT_RE.search(fold(text))
    if not m:
        return None
    n = int(m.group("n"))
    return n if 1 <= n <= 1000 else None
