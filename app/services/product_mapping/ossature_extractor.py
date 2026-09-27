"""Extracteur / classifieur déterministe OSSATURE_PLACO V1.

Sous-familles : RAIL | MONTANT | FOURRURE.
Jamais de valeur inventée — absence → null (UNKNOWN).
Longueur nominale : table explicite portée par la CategoryRule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.product_mapping import (
    CATEGORY_OSSATURE_PLACO,
    EXTRACTOR_VERSION_OSSATURE_V1,
    KIND_FOURRURE,
    KIND_MONTANT,
    KIND_RAIL,
)
from app.services.product_mapping.primitives.length import extract_bar_length_mm
from app.services.product_mapping.primitives.profile import extract_metal_profile
from app.services.product_mapping.primitives.textutil import fold as _fold
from app.services.product_mapping.rules.base import apply_normalization_specs
from app.services.product_mapping.rules.ossature_placo import (
    IDENTITY_KEYS,
    NOMINAL_LENGTH_MAP,
    NOMINAL_LENGTH_SPEC,
)

__all__ = [
    "ExtractionResult",
    "NOMINAL_LENGTH_MAP",
    "classify_kind",
    "extract_length_mm",
    "extract_ossature_placo",
    "extract_profile",
    "identity_attrs_sufficient",
    "is_accessory_or_noise",
    "nominal_length_mm",
]

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

_KIND_RAIL_RE = re.compile(r"\brails?\b", re.I)
_KIND_MONTANT_RE = re.compile(r"\bmontants?\b", re.I)
_KIND_FOURRURE_RE = re.compile(r"\bfourrures?\b", re.I)


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
    return extract_metal_profile(text)


def extract_length_mm(text: str) -> int | None:
    """Longueur barre — première valeur plausible (500–12000 mm)."""
    return extract_bar_length_mm(text)


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
    }
    # Longueur nominale posée par la normalisation déclarative de la règle
    apply_normalization_specs(attrs, (NOMINAL_LENGTH_SPEC,))
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
    return all(attrs.get(k) is not None for k in IDENTITY_KEYS)
