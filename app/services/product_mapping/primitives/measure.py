"""Primitives mesure génériques — volume (ml), masse (g), diamètre nominal (DN).

Aucune connaissance métier : ces trois grandeurs apparaissent dans beaucoup de
familles (peintures, enduits, colles, mortiers, plomberie…) et sont ramenées à
une unité de base entière pour rester comparables entre fournisseurs.

Les gardes d'unité sont volontairement strictes : une unité doit être collée ou
séparée par des espaces du nombre, et suivie d'une frontière de mot. Sans cela
« 806 lm » (lumens) deviendrait un volume et « 3G2,5 » (câble) une masse.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.product_mapping.primitives.textutil import (
    fold,
    parse_number,
    round_half_up,
)

_NUMBER = r"(?P<n>\d{1,5}(?:[.,]\d{1,3})?)"

_VOLUME_RE = re.compile(rf"\b{_NUMBER}\s*(?P<u>ml|cl|dl|litres?|l)\b")
_WEIGHT_RE = re.compile(
    rf"\b{_NUMBER}\s*(?P<u>kilogrammes?|kilos?|grammes?|kg|gr|g)\b"
)
# « DN 40 », « DN40 » — diamètre nominal normalisé, jamais une dimension libre.
_DN_RE = re.compile(r"\bdn\s*(?P<n>\d{1,4})\b")

_ML_PER_UNIT: dict[str, Decimal] = {
    "ml": Decimal(1),
    "cl": Decimal(10),
    "dl": Decimal(100),
    "l": Decimal(1000),
    "litre": Decimal(1000),
    "litres": Decimal(1000),
}

_G_PER_UNIT: dict[str, Decimal] = {
    "g": Decimal(1),
    "gr": Decimal(1),
    "gramme": Decimal(1),
    "grammes": Decimal(1),
    "kg": Decimal(1000),
    "kilo": Decimal(1000),
    "kilos": Decimal(1000),
    "kilogramme": Decimal(1000),
    "kilogrammes": Decimal(1000),
}

MIN_VOLUME_ML = 1
MAX_VOLUME_ML = 1_000_000  # 1 000 L
MIN_WEIGHT_G = 1
MAX_WEIGHT_G = 500_000  # 500 kg
MIN_DN = 1
MAX_DN = 2000


def extract_volume_ml(text: str) -> int | None:
    """Volume en millilitres — « 10 L », « 750 ml », « 33 cl ». None si absent."""
    match = _VOLUME_RE.search(fold(text or ""))
    if match is None:
        return None
    value = parse_number(match.group("n"))
    if value is None:
        return None
    millilitres = round_half_up(value * _ML_PER_UNIT[match.group("u")])
    if millilitres < MIN_VOLUME_ML or millilitres > MAX_VOLUME_ML:
        return None
    return millilitres


def extract_weight_g(text: str) -> int | None:
    """Masse en grammes — « 25 kg », « 500 g ». None si absente."""
    match = _WEIGHT_RE.search(fold(text or ""))
    if match is None:
        return None
    value = parse_number(match.group("n"))
    if value is None:
        return None
    grams = round_half_up(value * _G_PER_UNIT[match.group("u")])
    if grams < MIN_WEIGHT_G or grams > MAX_WEIGHT_G:
        return None
    return grams


def extract_dn(text: str) -> int | None:
    """Diamètre nominal — « DN 40 », « DN100 ». None si absent."""
    match = _DN_RE.search(fold(text or ""))
    if match is None:
        return None
    dn = int(match.group("n"))
    if dn < MIN_DN or dn > MAX_DN:
        return None
    return dn
