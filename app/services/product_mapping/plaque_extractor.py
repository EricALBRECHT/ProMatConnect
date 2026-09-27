"""Extracteur / classifieur déterministe PLAQUE_PLATRE V1.3.

Jamais de false inventé pour une info absente → null (unknown).
Les primitives texte / dimensions sont mutualisées (primitives/).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.product_mapping import (
    CATEGORY_PLAQUE_PLATRE,
    EXTRACTOR_VERSION_PLAQUE_V1,
)
from app.services.product_mapping.primitives.dimensions import (
    extract_dimensions_mm,
    extract_length_width_mm,
    extract_thickness_alone_mm,
)
from app.services.product_mapping.primitives.textutil import fold as _fold
from app.services.product_mapping.rules.plaque_platre import (
    BOOL_COMPAT_KEYS,
    DIM_KEYS,
    IDENTITY_KEYS,
)

__all__ = [
    "ExtractionResult",
    "diagnose_attribute_gaps",
    "dims_present",
    "extract_dimensions_mm",
    "extract_length_width_mm",
    "extract_plaque_platre",
    "extract_plaque_type_and_flags",
    "extract_thickness_alone_mm",
    "identity_attrs_sufficient",
    "infer_thickness_from_ba",
    "is_accessory_not_plaque",
    "looks_like_plaque_platre",
]

# Raisons d'analyse
REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"
REASON_NO_PMC_PRODUCT = "NO_PMC_PRODUCT"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REASON_AMBIGUOUS = "AMBIGUOUS"
REASON_ALREADY_MAPPED = "ALREADY_MAPPED"
REASON_EXACT = "EXACT"
REASON_HIGH = "HIGH"
REASON_REVIEW = "REVIEW"

# Raisons diagnostiques détaillées (gaps)
GAP_MISSING_LENGTH = "MISSING_LENGTH"
GAP_MISSING_WIDTH = "MISSING_WIDTH"
GAP_MISSING_THICKNESS = "MISSING_THICKNESS"
GAP_MISSING_TYPE = "MISSING_TYPE"
GAP_PARTIAL_DIMENSIONS = "PARTIAL_DIMENSIONS"
GAP_UNKNOWN_FUNCTIONAL_FLAG = "UNKNOWN_FUNCTIONAL_FLAG"
GAP_NOT_SIMPLE_PLAQUE = "NOT_SIMPLE_PLAQUE"
GAP_NO_PMC_FOR_VARIANT = "NO_PMC_FOR_VARIANT"

# Accessoires / non-plaques — exclusions déterministes (exemples Brico réels)
_ACCESSORY_PATTERNS = (
    r"\bvis\b",
    r"\bcheville",
    r"\bbande\b",
    r"\benduit\b",
    r"\bseau\b",
    r"\boutil",
    r"\bporte[- ]?plaque",
    r"\bleve[- ]?plaque",
    r"\blève[- ]?plaque",
    r"\bfixation",
    r"\bvisseuse",
    r"\bvisseau",
    r"\bcartouche\b",
    r"\bjoint\b",
    r"\bcolle\b",
    r"\bprimer\b",
    r"\bvisse",
    r"\bboulon",
    r"\becrou",
    r"\bécrou",
    r"\bpoignee",
    r"\bpoignée",
    r"\bmanchon\b",
    r"\bsupport\b",
    r"\bkit\b",
    r"\brouleau\b",
    r"\bpince\b",
    r"\bcouteau\b",
    r"\bspatule\b",
    r"\babribus",
    # V1.3 — faux positifs observés (pas des plaques simples)
    r"\bcrochet",
    r"\bgriffe\b",
    r"\bbatibox\b",
    r"\bboite\b",
    r"\bencastr",
    r"\bsous[- ]?couche",
    r"\bpeinture\b",
    r"\bmortier\b",
    r"\bporte[- ]?embout",
    r"\bdoublage\b",
    r"\bpolystyrene\b",
    r"\bpolyurethane\b",
    r"\bmontant",
    r"\bfourrure",
    r"\brails?\b",
    r"\bossature\b",
)
_ACCESSORY_RE = re.compile("|".join(_ACCESSORY_PATTERNS), re.I)

_PLAQUE_STRONG = re.compile(
    r"\b("
    r"plaque\s+(de\s+)?platre|"
    r"plaque\s+ba\s*\d+|"
    r"ba\s*13|ba\s*18|ba\s*10|ba\s*15|ba\s*25|"
    r"purelight|placo\s*ba|"
    r"plaque\s+standard|"
    r"plaque\s+hydro|"
    r"plaque\s+multifonction|"
    r"plaque\s+coupe[- ]?feu|"
    r"plaque\s+phonique|"
    r"plaque\s+legere|"
    r"knauf\s+plaque|siniat\s+plaque|gyproc"
    r")\b",
    re.I,
)

_FIRE_MARKERS = (
    "coupe-feu",
    "coupe feu",
    "anti-feu",
    "anti feu",
    "resistant au feu",
    "resistante au feu",
    "resistance au feu",
    "resistante feu",
    "resistant feu",
    "fei",
)

_BA_THICKNESS = re.compile(r"\bba\s*(\d{1,2})\b", re.I)
_HYDRO_RE = re.compile(r"\bhydro(?:fuge)?\b", re.I)


@dataclass(frozen=True)
class ExtractionResult:
    category_code: str | None
    attributes: dict[str, Any]
    confidence: float
    extractor_version: str
    classified: bool
    reason: str | None = None


def is_accessory_not_plaque(designation: str, category_path: str | None = None) -> bool:
    """True si accessoire / doublage / ossature — pas une plaque simple."""
    blob = _fold(f"{designation} {category_path or ''}")
    if not _ACCESSORY_RE.search(blob):
        return False
    # Exception : vraie plaque citant un accessoire + dims type plaque
    if _PLAQUE_STRONG.search(blob) and "doublage" not in blob:
        length, width, thickness = extract_dimensions_mm(designation)
        if (
            length
            and width
            and thickness
            and length >= 1200
            and width >= 600
            and 6 <= thickness <= 30
        ):
            return False
    return True


def looks_like_plaque_platre(designation: str, category_path: str | None = None) -> bool:
    if is_accessory_not_plaque(designation, category_path):
        return False
    blob = _fold(f"{designation} {category_path or ''}")
    if _PLAQUE_STRONG.search(blob):
        return True
    if "plaque" in blob and "platre" in blob:
        return True
    return False


def extract_plaque_type_and_flags(text: str) -> dict[str, Any]:
    blob = _fold(text)
    out: dict[str, Any] = {
        "type": None,
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }

    if _HYDRO_RE.search(blob):
        out["hydrofuge"] = True
    if any(k in blob for k in _FIRE_MARKERS):
        out["fire_resistant"] = True
    if any(k in blob for k in ("phonique", "acoustique")):
        out["acoustic"] = True

    if "purelight" in blob or "legere" in blob:
        out["type"] = "legere"
    elif _HYDRO_RE.search(blob):
        out["type"] = "hydrofuge"
        out["hydrofuge"] = True
    elif "multifonction" in blob:
        out["type"] = "multifonctions"
    elif "phonique" in blob or "acoustique" in blob:
        out["type"] = "phonique"
        out["acoustic"] = True
    elif any(k in blob for k in _FIRE_MARKERS):
        out["type"] = "feu"
        out["fire_resistant"] = True
    elif "standard" in blob or re.search(r"\bba\s*\d+", blob):
        out["type"] = "standard"

    return out


def infer_thickness_from_ba(text: str) -> int | None:
    """Épaisseur déduite du code commercial BA — « BA13 » → 13 mm."""
    m = _BA_THICKNESS.search(_fold(text))
    if not m:
        return None
    th = int(m.group(1))
    if 6 <= th <= 30:
        return th
    return None


def extract_plaque_platre(
    *,
    designation: str,
    category_path: str | None = None,
) -> ExtractionResult:
    if is_accessory_not_plaque(designation, category_path):
        return ExtractionResult(
            category_code=None,
            attributes={},
            confidence=0.0,
            extractor_version=EXTRACTOR_VERSION_PLAQUE_V1,
            classified=False,
            reason=REASON_NOT_THIS_CATEGORY,
        )
    classified = looks_like_plaque_platre(designation, category_path)
    if not classified:
        return ExtractionResult(
            category_code=None,
            attributes={},
            confidence=0.0,
            extractor_version=EXTRACTOR_VERSION_PLAQUE_V1,
            classified=False,
            reason=REASON_NOT_THIS_CATEGORY,
        )

    length, width, thickness = extract_dimensions_mm(designation)
    if length is None or width is None:
        lw_l, lw_w = extract_length_width_mm(designation)
        length = length or lw_l
        width = width or lw_w
    if thickness is None:
        thickness = extract_thickness_alone_mm(designation)
    if thickness is None:
        thickness = infer_thickness_from_ba(designation)
    flags = extract_plaque_type_and_flags(designation)
    attrs: dict[str, Any] = {
        "length_mm": length,
        "width_mm": width,
        "thickness_mm": thickness,
        "type": flags["type"],
        "hydrofuge": flags["hydrofuge"],
        "fire_resistant": flags["fire_resistant"],
        "acoustic": flags["acoustic"],
    }
    conf = 0.4
    if length and width and thickness:
        conf += 0.4
    if flags["type"]:
        conf += 0.2
    return ExtractionResult(
        category_code=CATEGORY_PLAQUE_PLATRE,
        attributes=attrs,
        confidence=min(conf, 1.0),
        extractor_version=EXTRACTOR_VERSION_PLAQUE_V1,
        classified=True,
        reason=None,
    )


def identity_attrs_sufficient(attrs: dict[str, Any]) -> bool:
    return all(attrs.get(k) is not None for k in IDENTITY_KEYS)


def dims_present(attrs: dict[str, Any]) -> bool:
    return all(attrs.get(k) is not None for k in DIM_KEYS)


def diagnose_attribute_gaps(
    attrs: dict[str, Any],
    *,
    match_reason: str | None = None,
) -> list[str]:
    """Raisons explicites empêchant EXACT."""
    gaps: list[str] = []
    if attrs.get("length_mm") is None:
        gaps.append(GAP_MISSING_LENGTH)
    if attrs.get("width_mm") is None:
        gaps.append(GAP_MISSING_WIDTH)
    if attrs.get("thickness_mm") is None:
        gaps.append(GAP_MISSING_THICKNESS)
    if attrs.get("type") is None:
        gaps.append(GAP_MISSING_TYPE)
    present = sum(1 for k in DIM_KEYS if attrs.get(k) is not None)
    if 0 < present < len(DIM_KEYS):
        gaps.append(GAP_PARTIAL_DIMENSIONS)
    # Flags fonctionnels absents ≠ false — signal diagnostique seulement
    if (
        attrs.get("type") in {None, "standard"}
        and all(attrs.get(k) is None for k in BOOL_COMPAT_KEYS)
        and match_reason == REASON_REVIEW
    ):
        gaps.append(GAP_UNKNOWN_FUNCTIONAL_FLAG)
    if match_reason == REASON_NO_PMC_PRODUCT:
        gaps.append(GAP_NO_PMC_FOR_VARIANT)
    return gaps
