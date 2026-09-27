"""Conditionnement effectif SupplierProduct → comparateur (sans écriture DB)."""

from __future__ import annotations

import re
from decimal import Decimal

from app.models import SupplierProduct
from app.services.product_mapping.primitives.packaging import extract_piece_count
from app.services.product_mapping.primitives.textutil import fold

# Kits / meubles / compositions : « N pièces » = éléments hétérogènes du set,
# pas le conditionnement d'unités PMC identiques.
_COMMERCIAL_SET_RE = re.compile(
    r"\b("
    r"ensemble|kits?\b|set\b|meubles?|composition|assortiment|"
    r"accessoires?|collection|gamme|coffrets?"
    r")\b"
)

# Packs mixtes (cheville + vis, pattes à vis, etc.) : le N du libellé
# n'est pas fiable comme reference_quantity d'un Product PMC unitaire.
_COMBO_PACK_RE = re.compile(
    r"(?:\+|/\s*|\bavec\b|\bet\b)\s*(?:vis|chevilles?|pattes?\s+a\s+vis)\b"
    r"|\bpattes?\s+a\s+vis\b"
    r"|\bvis\b.+\bchevilles?\b"
    r"|\bchevilles?\b.+\b(?:vis|pattes?)\b",
)

# « (2 pièces) » entre parenthèses = composition commerciale, pas un sachet.
_PAREN_SMALL_PACK_RE = re.compile(r"\(\s*(?P<n>\d{1,2})\s*pieces?\s*\)")

# Familles où « N pièces » désigne des unités consommables identiques.
_IDENTICAL_BULK_RE = re.compile(
    r"\b("
    r"vis\b|chevilles?|clous?|boulons?|ecrous?|rondelles?|rivets?|"
    r"pointes?|agrafes?|tire[- ]?fonds?|goujons?|inserts?|"
    r"joints?\b|serre[- ]?joints?|"
    r"rails?\b|montants?|fourrures?"
    r")\b"
)


def resolve_reference_quantity(sp: SupplierProduct) -> Decimal:
    """Unités PMC par conditionnement vendu.

    Les imports Brico laissent souvent ``reference_quantity=1`` alors que la
    désignation indique un pack de N unités identiques (« 500 pièces »,
    « boîte de 200 »). On dérive alors le conditionnement depuis le libellé,
    sans modifier la ligne catalogue.

    Garde-fous (données catalogue) :
    - ne jamais écraser une ``reference_quantity`` déjà renseignée (> 1) ;
    - ignorer kit / ensemble / meuble / coffret / accessoire ;
    - ignorer les packs mixtes (« cheville + vis », « avec vis ») ;
    - n'accepter N que pour une famille d'unités identiques (visserie,
      chevilles, rails/montants, joints, etc.).
    """
    base = sp.reference_quantity
    if base is None or base <= 0:
        base = Decimal("1")
    if base != Decimal("1"):
        return base

    designation = sp.designation or ""
    blob = fold(designation)
    if _COMMERCIAL_SET_RE.search(blob):
        return base
    if _COMBO_PACK_RE.search(blob):
        return base
    paren = _PAREN_SMALL_PACK_RE.search(blob)
    if paren is not None and int(paren.group("n")) <= 12:
        return base

    pieces = extract_piece_count(designation)
    if pieces is None or pieces <= 1:
        return base

    if _IDENTICAL_BULK_RE.search(blob):
        return Decimal(pieces)
    return base
