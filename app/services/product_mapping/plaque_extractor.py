"""Extracteur / classifieur déterministe PLAQUE_PLATRE V1.3.

Jamais de false inventé pour une info absente → null (unknown).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.models.product_mapping import (
    CATEGORY_PLAQUE_PLATRE,
    EXTRACTOR_VERSION_PLAQUE_V1,
)

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


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _parse_number(raw: str) -> Decimal | None:
    text = raw.strip().replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _to_mm(value: Decimal, unit: str | None) -> int | None:
    """Conversion en mm ; arrondi half-up (1,25 cm → 13 mm)."""
    u = (unit or "").lower().replace(" ", "")
    if u in {"m", "ml", "metre"}:
        mm = value * 1000
    elif u in {"cm"}:
        mm = value * 10
    elif u in {"mm", ""}:
        if unit is None and value < Decimal("20"):
            mm = value * 1000
        else:
            mm = value
    else:
        mm = value
    mm_i = int(mm.to_integral_value(rounding=ROUND_HALF_UP))
    if mm_i <= 0 or mm_i > 10000:
        return None
    return mm_i


_DIM_TRIPLE = re.compile(
    r"(?:l\.?\s*)?(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]\s*"
    r"(?:l\.?\s*)?(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?\s*[x×*]\s*"
    r"(?:epaisseur|épaisseur|ep\.?\s*|ép\.?\s*|e\.?\s*)?(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
    re.I,
)
_DIM_LABELED = re.compile(
    r"l\.?\s*(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]?\s*"
    r"l\.?\s*(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?\s*[x×*]?\s*"
    r"(?:epaisseur|épaisseur|ep\.?|ép\.?|e\.?)\s*(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
    re.I,
)

# L × l sans épaisseur (labels optionnels) — ex. "2,50 x 1,20 m", "2,5 X 1,2M"
_DIM_PAIR = re.compile(
    r"(?:l\.?\s*)?(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]\s*"
    r"(?:l\.?\s*)?(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?",
    re.I,
)

# Épaisseur seule — ex. "ép. 13 mm", "épaisseur 1,25 cm"
_THICKNESS_ONLY = re.compile(
    r"(?:epaisseur|épaisseur|ép\.?|ep\.?)\s*(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
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


def _apply_shared_unit(
    ul: str | None, uw: str | None
) -> tuple[str | None, str | None]:
    """Si une seule unité est présente sur L×l, l'appliquer aux deux."""
    if ul and not uw:
        return ul, ul
    if uw and not ul:
        return uw, uw
    return ul, uw


def _pair_to_mm(
    l_raw: str, w_raw: str, ul: str | None, uw: str | None
) -> tuple[int | None, int | None]:
    l = _parse_number(l_raw)
    w = _parse_number(w_raw)
    if l is None or w is None:
        return None, None
    ul, uw = _apply_shared_unit(ul, uw)
    length = _to_mm(l, ul)
    width = _to_mm(w, uw)
    if length and width:
        if width > length:
            length, width = width, length
        return length, width
    return None, None


def extract_dimensions_mm(text: str) -> tuple[int | None, int | None, int | None]:
    normalized = text.replace("×", "x")
    for pattern in (_DIM_LABELED, _DIM_TRIPLE):
        m = pattern.search(normalized)
        if not m:
            continue
        # Rejeter « 13 + 80 » (doublage) — le groupe t ne doit pas être suivi de +
        t_span = m.end("t")
        if t_span < len(normalized) and normalized[t_span : t_span + 2].lstrip().startswith(
            "+"
        ):
            continue
        l = _parse_number(m.group("l"))
        w = _parse_number(m.group("w"))
        t = _parse_number(m.group("t"))
        if l is None or w is None or t is None:
            continue
        ul, uw = _apply_shared_unit(m.group("ul"), m.group("uw"))
        length = _to_mm(l, ul)
        width = _to_mm(w, uw)
        t_unit = m.group("ut")
        if t_unit:
            thickness = _to_mm(t, t_unit)
        else:
            thickness = (
                int(t.to_integral_value(rounding=ROUND_HALF_UP))
                if t < 100
                else _to_mm(t, "mm")
            )
        if length and width and thickness:
            if width > length:
                length, width = width, length
            return length, width, thickness
    return None, None, None


def extract_length_width_mm(text: str) -> tuple[int | None, int | None]:
    """L × l sans épaisseur — labels optionnels.

    Exemples Brico :
    - « BA 13 hydrofuge NF - 2,50 x 1,20 m »
    - « Plaque BA13 NF standard - 2,5 X 1,2M »
    - « 120x250 cm »
    - « 250 cm x 120 cm »
    """
    m = _DIM_PAIR.search(text.replace("×", "x"))
    if not m:
        return None, None
    return _pair_to_mm(m.group("l"), m.group("w"), m.group("ul"), m.group("uw"))


def extract_thickness_alone_mm(text: str) -> int | None:
    """Épaisseur isolée — ex. « ép. 13 mm », « épaisseur 1,25 cm »."""
    m = _THICKNESS_ONLY.search(text.replace("×", "x"))
    if not m:
        return None
    t = _parse_number(m.group("t"))
    if t is None:
        return None
    ut = m.group("ut")
    if ut:
        return _to_mm(t, ut)
    if t < 100:
        return int(t.to_integral_value(rounding=ROUND_HALF_UP))
    return _to_mm(t, "mm")


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
    return all(
        attrs.get(k) is not None
        for k in ("length_mm", "width_mm", "thickness_mm", "type")
    )


def dims_present(attrs: dict[str, Any]) -> bool:
    return all(
        attrs.get(k) is not None for k in ("length_mm", "width_mm", "thickness_mm")
    )


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
    present = sum(
        1
        for k in ("length_mm", "width_mm", "thickness_mm")
        if attrs.get(k) is not None
    )
    if 0 < present < 3:
        gaps.append(GAP_PARTIAL_DIMENSIONS)
    # Flags fonctionnels absents ≠ false — signal diagnostique seulement
    if (
        attrs.get("type") in {None, "standard"}
        and attrs.get("hydrofuge") is None
        and attrs.get("fire_resistant") is None
        and attrs.get("acoustic") is None
        and match_reason == REASON_REVIEW
    ):
        gaps.append(GAP_UNKNOWN_FUNCTIONAL_FLAG)
    if match_reason == REASON_NO_PMC_PRODUCT:
        gaps.append(GAP_NO_PMC_FOR_VARIANT)
    return gaps
