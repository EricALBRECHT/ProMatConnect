"""Extracteur / classifieur déterministe OSSATURE_PLACO V1.

Sous-familles : RAIL | MONTANT | FOURRURE.
Jamais de valeur inventée — absence → null (UNKNOWN).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.models.product_mapping import (
    CATEGORY_OSSATURE_PLACO,
    EXTRACTOR_VERSION_OSSATURE_V1,
    KIND_FOURRURE,
    KIND_MONTANT,
    KIND_RAIL,
)

REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"
REASON_NO_PMC_PRODUCT = "NO_PMC_PRODUCT"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REASON_AMBIGUOUS = "AMBIGUOUS"
REASON_ALREADY_MAPPED = "ALREADY_MAPPED"
REASON_EXACT = "EXACT"
REASON_HIGH = "HIGH"
REASON_REVIEW = "REVIEW"

_ACCESSORY_PATTERNS = (
    r"\bsuspente",
    r"\beclisse",
    r"\bcorniere",
    r"\bcavalier",
    r"\bappui\b",
    r"\bfixation",
    r"\bkit\b",
    r"\bconnecteur",
    r"\braccord",
    r"\bbride\b",
    r"\brallonge",
    r"\blisse\b",
    r"\bcrochet",
    r"\bchaussure",
    r"\bbasket",
    r"\bporte\b",
    r"\bcoulissant",
    r"\bscie\b",
    r"\bguidage\b",
    r"\blaque",
    r"\bmural\b",
    r"\bsupport\b",
    r"\bliaison",
    r"\bpanneau",
    r"\bvis\b",
    r"\bcheville",
    r"\bverrou\b",
    r"\bmoraillon",
    r"\bensemble\b",
)
_ACCESSORY_RE = re.compile("|".join(_ACCESSORY_PATTERNS), re.I)

# Faux positif linguistique : chaussures / baskets « montantes »
_MONTANTE_SHOE_RE = re.compile(
    r"\b(chaussure|basket|botte|safety).{0,40}montante|\bmontante.{0,20}(chaussure|securite|s1p|s3)\b",
    re.I,
)
_MONTANTES_WORD_RE = re.compile(r"\bmontantes?\b", re.I)

_PROFILE_RE = re.compile(
    r"\b(?P<p>"
    r"R\s*48|R\s*70|R\s*90|R\s*100|"
    r"M\s*48|M\s*70|M\s*90|"
    r"F\s*45|"
    r"M48\d{2}"  # ex. M4835 → M48
    r")\b",
    re.I,
)
_RAIL_DE_N_RE = re.compile(r"\brail\s+de\s+(?P<n>48|70|90|100)\b", re.I)
_MONTANT_N_RE = re.compile(r"\bmontant\s+(?P<n>48|70|90)\b", re.I)
_FOURRURE_N_RE = re.compile(r"\bfourrure\s+(?:profilee\s+)?(?:galvanisee?\s+)?(?P<n>45)\b", re.I)

_KIND_RAIL_RE = re.compile(r"\brails?\b", re.I)
_KIND_MONTANT_RE = re.compile(r"\bmontants?\b", re.I)
_KIND_FOURRURE_RE = re.compile(r"\bfourrures?\b", re.I)

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


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _parse_number(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _to_mm(value: Decimal, unit: str) -> int | None:
    u = unit.lower().replace(" ", "")
    if u in {"m", "ml"}:
        mm = value * 1000
    elif u == "cm":
        mm = value * 10
    elif u == "mm":
        mm = value
    else:
        return None
    mm_i = int(mm.to_integral_value(rounding=ROUND_HALF_UP))
    if mm_i < 500 or mm_i > 12000:
        return None
    return mm_i


def _norm_profile(raw: str) -> str | None:
    t = re.sub(r"\s+", "", raw.upper())
    if t.startswith("M48") and len(t) > 3 and t[3:].isdigit():
        return "M48"
    if t in {"R48", "R70", "R90", "R100", "M48", "M70", "M90", "F45"}:
        return t
    return None


@dataclass(frozen=True)
class ExtractionResult:
    category_code: str | None
    attributes: dict[str, Any]
    confidence: float
    extractor_version: str
    classified: bool
    reason: str | None = None


def is_accessory_or_noise(designation: str) -> bool:
    blob = _fold(designation)
    if _ACCESSORY_RE.search(blob):
        return True
    if _MONTANTE_SHOE_RE.search(blob):
        return True
    # « montantes » sans contexte ossature (chaussures)
    if _MONTANTES_WORD_RE.search(blob) and not re.search(
        r"\bmontants?\b|\bM\s*\d{2}\b", designation, re.I
    ):
        return True
    return False


def extract_profile(text: str) -> str | None:
    m = _PROFILE_RE.search(text)
    if m:
        return _norm_profile(m.group("p"))
    m = _RAIL_DE_N_RE.search(text)
    if m:
        return f"R{m.group('n')}"
    m = _MONTANT_N_RE.search(_fold(text))
    if m:
        return f"M{m.group('n')}"
    m = _FOURRURE_N_RE.search(_fold(text))
    if m:
        return "F45"
    return None


def extract_length_mm(text: str) -> int | None:
    """Longueur barre — première valeur plausible (500–12000 mm)."""
    for pattern in _LENGTH_PATTERNS:
        for m in pattern.finditer(text.replace("×", "x")):
            n = _parse_number(m.group("l"))
            if n is None:
                continue
            unit = m.groupdict().get("u") or "mm"
            # Pattern « 48 x 2490 mm » : groupe l est déjà en mm
            if "u" not in m.groupdict() or m.groupdict().get("u") is None:
                if 500 <= int(n) <= 12000:
                    return int(n)
                continue
            mm = _to_mm(n, unit)
            if mm is not None:
                return mm
    return None


def classify_kind(designation: str, profile: str | None) -> str | None:
    """RAIL | MONTANT | FOURRURE | None si non classifiable."""
    if is_accessory_or_noise(designation):
        return None
    blob = _fold(designation)

    # Profil explicite → kind
    if profile:
        if profile.startswith("R"):
            return KIND_RAIL
        if profile.startswith("M"):
            return KIND_MONTANT
        if profile.startswith("F"):
            return KIND_FOURRURE

    # Mots-clés (ordre : fourrure / montant / rail pour éviter collisions)
    if _KIND_FOURRURE_RE.search(blob):
        return KIND_FOURRURE
    if _KIND_MONTANT_RE.search(blob):
        return KIND_MONTANT
    if _KIND_RAIL_RE.search(blob):
        # Rail placo si profil ou galvanisé / NF / profilé en U
        if any(
            k in blob
            for k in ("galvanis", "profile", " nf", "acier", "placo", "cloison")
        ):
            return KIND_RAIL
        # Sinon douteux (rail laqué, porte…) → déjà filtré accessoires ;
        # sans profil ni indice placo → rejeter
        return None
    return None


# Longueurs commerciales nominales — table explicite (PAS une tolérance ±)
# length_mm fournisseur → nominal_length_mm pour matching PMC
NOMINAL_LENGTH_MAP: dict[int, int] = {
    2490: 2500,
    2500: 2500,
    2990: 3000,
    3000: 3000,
}


def nominal_length_mm(length_mm: int | None) -> int | None:
    """Longueur commerciale nominale. Hors table → inchangée (pas d'arrondi)."""
    if length_mm is None:
        return None
    return NOMINAL_LENGTH_MAP.get(length_mm, length_mm)


def extract_ossature_placo(*, designation: str) -> ExtractionResult:
    if is_accessory_or_noise(designation):
        return ExtractionResult(
            category_code=None,
            attributes={},
            confidence=0.0,
            extractor_version=EXTRACTOR_VERSION_OSSATURE_V1,
            classified=False,
            reason=REASON_NOT_THIS_CATEGORY,
        )

    profile = extract_profile(designation)
    kind = classify_kind(designation, profile)
    if kind is None:
        return ExtractionResult(
            category_code=None,
            attributes={},
            confidence=0.0,
            extractor_version=EXTRACTOR_VERSION_OSSATURE_V1,
            classified=False,
            reason=REASON_NOT_THIS_CATEGORY,
        )

    length = extract_length_mm(designation)
    attrs: dict[str, Any] = {
        "kind": kind,
        "profile": profile,
        "length_mm": length,  # réel fournisseur — jamais modifié
        "nominal_length_mm": nominal_length_mm(length),
    }
    conf = 0.4
    if profile:
        conf += 0.3
    if length:
        conf += 0.3
    return ExtractionResult(
        category_code=CATEGORY_OSSATURE_PLACO,
        attributes=attrs,
        confidence=min(conf, 1.0),
        extractor_version=EXTRACTOR_VERSION_OSSATURE_V1,
        classified=True,
        reason=None,
    )


def identity_attrs_sufficient(attrs: dict[str, Any]) -> bool:
    """Identité matching : kind + profile + nominal_length_mm."""
    return all(
        attrs.get(k) is not None for k in ("kind", "profile", "nominal_length_mm")
    )
