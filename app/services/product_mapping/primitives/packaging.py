"""Primitive conditionnement — « Lot de 10 », « Boîte de 200 », « 1000 pièces ».

Non utilisée par l'identité de matching des catégories actuelles : un même
produit vendu en boîte de 200 ou en seau de 1000 reste le même produit.
"""

from __future__ import annotations

import re

from app.services.product_mapping.primitives.textutil import fold

# Contenants dénombrés. La garde d'unité évite de lire une masse ou un volume
# comme un nombre de pièces (« boîte de 2 kg » n'est pas un lot de 2).
_UNIT_GUARD = r"(?!\s*(?:kg|g\b|mg|l\b|ml\b|cl\b|m\b|mm\b|cm\b|v\b|w\b))"

_LOT_RE = re.compile(
    r"\b(?:lot|pack|paquet|botte|colis|boite|bte|seau|sachet)\s*(?:de\s*)?"
    rf"(?P<n>\d{{1,4}})\b{_UNIT_GUARD}",
    re.I,
)

# « 200 pièces », « 10 pcs », « 1 000 pièces »
_PIECES_RE = re.compile(r"\b(?P<n>\d{1,3}(?:\s\d{3})*|\d{1,5})\s*(?:pieces?|pcs?)\b", re.I)

MAX_PIECE_COUNT = 100000


def extract_lot_quantity(text: str) -> int | None:
    """Nombre de pièces par lot / boîte / seau — None si non exprimé."""
    m = _LOT_RE.search(fold(text))
    if not m:
        return None
    n = int(m.group("n"))
    return n if 1 <= n <= 1000 else None


def extract_piece_count(text: str) -> int | None:
    """Nombre de pièces conditionnées, « N pièces » prioritaire sur le contenant.

    Plage large (jusqu'à 100 000) : les seaux de visserie dépassent le cadre
    « lot » de ``extract_lot_quantity``.
    """
    blob = fold(text)
    for pattern in (_PIECES_RE, _LOT_RE):
        m = pattern.search(blob)
        if m is None:
            continue
        n = int(m.group("n").replace(" ", ""))
        if 1 <= n <= MAX_PIECE_COUNT:
            return n
    return None
