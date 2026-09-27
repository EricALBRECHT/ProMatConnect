"""Extracteur / classifieur déterministe VIS_PLACO V1.

Identité = type « placo » + diamètre + longueur. Le conditionnement
(200 pièces, seau de 1000, boîte de 2 kg) est un attribut optionnel : il ne
distingue pas deux produits.

Jamais de valeur inventée : information absente → null (UNKNOWN), jamais false.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.product_mapping import (
    CATEGORY_VIS_PLACO,
    EXTRACTOR_VERSION_VIS_V1,
)
from app.services.product_mapping.primitives.diameter import (
    extract_diameter_length_mm,
)
from app.services.product_mapping.primitives.packaging import extract_piece_count
from app.services.product_mapping.primitives.textutil import fold as _fold

__all__ = [
    "ExtractionResult",
    "TYPE_PLACO",
    "extract_vis_placo",
    "is_excluded",
    "is_vis_placo",
]

TYPE_PLACO = "placo"
REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"

# Outils, autres supports, quincaillerie : jamais des vis à plaque de plâtre.
_EXCLUDE_RE = re.compile(
    "|".join(
        (
            r"visseuse",
            r"embout",
            r"\bbois\b",
            r"terrasse",
            r"beton",
            r"cheville",
            r"boulon",
            r"tirefond",
            r"agglo",
            r"multi[\s-]?usage",
            r"\btole\b",
            r"assortiment",
            r"\bkits?\b",
        )
    ),
    re.I,
)

# Le mot « vis » doit être présent : « visseuse » / « vissage » ne comptent pas.
_VIS_RE = re.compile(r"\bvis\b", re.I)

# Signaux plaque de plâtre. « autoperceuse » (tôle, tuile) est volontairement
# absent : seules les formes autoperçante / autoperforante / autoforeuse valent.
# Le motif « plaque … plâtre » tolère les graphies fournisseur réellement
# rencontrées : « plaq platre », « plaque plâtre » (sans de), « plaque de pâtre ».
_PLACO_RE = re.compile(
    "|".join(
        (
            r"plaq(?:ues?)?\.?\s*(?:de\s+)?pl?atre",
            r"placo",
            r"trompette",
            r"\bttpc\b",
            r"autoperforant(?:e|es)?\b",
            r"autopercant(?:e|es)?\b",
            r"autoforeuses?\b",
        )
    ),
    re.I,
)


@dataclass(frozen=True)
class ExtractionResult:
    category_code: str | None
    attributes: dict[str, Any]
    confidence: float
    extractor_version: str
    classified: bool
    reason: str | None = None


def is_excluded(designation: str) -> bool:
    """Motifs disqualifiants — outils, autres matériaux, quincaillerie."""
    return bool(_EXCLUDE_RE.search(_fold(designation)))


def is_vis_placo(designation: str) -> bool:
    """« vis » + un signal plaque de plâtre, hors motifs disqualifiants."""
    blob = _fold(designation)
    if _EXCLUDE_RE.search(blob):
        return False
    return bool(_VIS_RE.search(blob) and _PLACO_RE.search(blob))


def _rejected() -> ExtractionResult:
    return ExtractionResult(
        category_code=None,
        attributes={},
        confidence=0.0,
        extractor_version=EXTRACTOR_VERSION_VIS_V1,
        classified=False,
        reason=REASON_NOT_THIS_CATEGORY,
    )


def extract_vis_placo(
    designation: str, category_path: str | None = None
) -> ExtractionResult:
    """Classification + extraction diamètre / longueur / conditionnement."""
    if not is_vis_placo(designation):
        return _rejected()

    diameter_mm, length_mm = extract_diameter_length_mm(designation)
    attrs: dict[str, Any] = {
        "type": TYPE_PLACO,
        "diameter_mm": diameter_mm,
        "length_mm": length_mm,
        # Hors identité : même vis, conditionnement différent.
        "packaging_qty": extract_piece_count(designation),
    }
    confidence = 0.4 + (0.3 if diameter_mm is not None else 0.0) + (
        0.3 if length_mm is not None else 0.0
    )
    return ExtractionResult(
        category_code=CATEGORY_VIS_PLACO,
        attributes=attrs,
        confidence=confidence,
        extractor_version=EXTRACTOR_VERSION_VIS_V1,
        classified=True,
        reason=None,
    )
