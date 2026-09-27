"""Extracteur déterministe PLAQUE_PLATRE — dimensions + type + flags UNKNOWN.

Jamais de false inventé pour une info absente → null (unknown).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.product_mapping import (
    CATEGORY_PLAQUE_PLATRE,
    EXTRACTOR_VERSION_PLAQUE_V1,
)

# Types alignés sur Product.attributes.type du seed BA13
PLAQUE_TYPES = frozenset(
    {"standard", "hydrofuge", "multifonctions", "legere", "feu", "phonique"}
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
    """Convertit m / cm / mm → mm entier ; unit None suppose mm si >= 100 sinon m si < 10."""
    u = (unit or "").lower().replace(" ", "")
    if u in {"m", "ml", "mètre", "metre"}:
        mm = int((value * 1000).to_integral_value())
    elif u in {"cm"}:
        mm = int((value * 10).to_integral_value())
    elif u in {"mm", ""}:
        # Heuristique : 2.5 / 1.2 → mètres ; 2500 → déjà mm
        if unit is None and value < Decimal("20"):
            mm = int((value * 1000).to_integral_value())
        else:
            mm = int(value.to_integral_value())
    else:
        mm = int(value.to_integral_value())
    if mm <= 0 or mm > 10000:
        return None
    return mm


# 2500 x 1200 x 13  |  2500×1200×13mm  |  L.2,50 x l.1,20m x Ep.13mm
_DIM_TRIPLE = re.compile(
    r"(?:l\.?\s*)?(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]\s*"
    r"(?:l\.?\s*)?(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?\s*[x×*]\s*"
    r"(?:ep\.?\s*|e\.?\s*)?(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
    re.I,
)
_DIM_LABELED = re.compile(
    r"l\.?\s*(?P<l>\d+(?:[.,]\d+)?)\s*(?P<ul>mm|cm|m)?\s*[x×*]?\s*"
    r"l\.?\s*(?P<w>\d+(?:[.,]\d+)?)\s*(?P<uw>mm|cm|m)?\s*[x×*]?\s*"
    r"(?:ep\.?|ép\.?|e\.?)\s*(?P<t>\d+(?:[.,]\d+)?)\s*(?P<ut>mm|cm|m)?",
    re.I,
)


@dataclass(frozen=True)
class ExtractionResult:
    category_code: str | None
    attributes: dict[str, Any]
    confidence: float
    extractor_version: str
    classified: bool


def looks_like_plaque_platre(designation: str, category_path: str | None = None) -> bool:
    blob = _fold(f"{designation} {category_path or ''}")
    if "plaque" not in blob:
        return False
    markers = (
        "platre",
        "plâtre",
        "ba13",
        "ba 13",
        "ba18",
        "ba 18",
        "ba10",
        "placo",
        "purelight",
        "knauf",
        "siniat",
        "gyproc",
    )
    # platre already folded without accent → platre
    markers_fold = tuple(_fold(m) for m in markers)
    return any(m in blob for m in markers_fold) or "platre" in blob


def extract_dimensions_mm(text: str) -> tuple[int | None, int | None, int | None]:
    """Retourne (length_mm, width_mm, thickness_mm) ou None par champ."""
    for pattern in (_DIM_LABELED, _DIM_TRIPLE):
        m = pattern.search(text.replace("×", "x"))
        if not m:
            continue
        l = _parse_number(m.group("l"))
        w = _parse_number(m.group("w"))
        t = _parse_number(m.group("t"))
        if l is None or w is None or t is None:
            continue
        length = _to_mm(l, m.group("ul"))
        width = _to_mm(w, m.group("uw"))
        # épaisseur : souvent mm même sans unité
        t_unit = m.group("ut")
        if t_unit:
            thickness = _to_mm(t, t_unit)
        else:
            thickness = int(t.to_integral_value()) if t < 100 else _to_mm(t, "mm")
        if length and width and thickness:
            # Normaliser L >= W pour formats plaque
            if width > length:
                length, width = width, length
            return length, width, thickness
    return None, None, None


def extract_plaque_type_and_flags(text: str) -> dict[str, Any]:
    """type + flags bool|None — jamais false inventé."""
    blob = _fold(text)
    out: dict[str, Any] = {
        "type": None,
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }

    # Flags explicites
    if any(k in blob for k in ("hydrofuge", "hydro ", " ba13 h", "h1 ", " green")):
        out["hydrofuge"] = True
    if any(k in blob for k in ("coupe-feu", "coupe feu", "anti-feu", "fei", "feu ")):
        # ne pas matcher "feu" seul trop agressif — "coupe-feu" / "anti-feu"
        if any(k in blob for k in ("coupe-feu", "coupe feu", "anti-feu", "anti feu", "fei")):
            out["fire_resistant"] = True
    if any(k in blob for k in ("phonique", "acoustique", "sound")):
        out["acoustic"] = True

    # Type (aligné seed PMC)
    if "purelight" in blob or "legere" in blob:
        out["type"] = "legere"
    elif "hydrofuge" in blob or (" hydro" in blob and "plaque" in blob):
        out["type"] = "hydrofuge"
        out["hydrofuge"] = True
    elif "multifonction" in blob:
        out["type"] = "multifonctions"
    elif "phonique" in blob or "acoustique" in blob:
        out["type"] = "phonique"
        out["acoustic"] = True
    elif any(k in blob for k in ("coupe-feu", "coupe feu", "anti-feu", "anti feu")):
        out["type"] = "feu"
        out["fire_resistant"] = True
    elif "standard" in blob or "ba13" in blob or "ba 13" in blob:
        # BA13 sans qualificatif → standard ; flags restent None (unknown)
        out["type"] = "standard"

    return out


def extract_plaque_platre(
    *,
    designation: str,
    category_path: str | None = None,
) -> ExtractionResult:
    """Extraction déterministe — category None si non classifié."""
    classified = looks_like_plaque_platre(designation, category_path)
    if not classified:
        return ExtractionResult(
            category_code=None,
            attributes={},
            confidence=0.0,
            extractor_version=EXTRACTOR_VERSION_PLAQUE_V1,
            classified=False,
        )

    length, width, thickness = extract_dimensions_mm(designation)
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
    # Confiance : dimensions + type
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
    )
