"""Familles DIMENSIONAL_FASTENER à fort volume — enregistrement compact.

Une famille = classify regex + type + bornes Ø/L + prefixes PMC.
Réutilise extract_typed_fastener / generic_matcher / pipeline.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Callable

from app.services.product_mapping.dimensional_fastener_shared import (
    ExtractionResult,
    extract_typed_fastener,
)
from app.services.product_mapping.identity_models import DIMENSIONAL_FASTENER
from app.services.product_mapping.primitives.textutil import fold as _fold
from app.services.product_mapping.rules.base import CategoryRule, register_rule

# ---------------------------------------------------------------------------
# Specs familles
# ---------------------------------------------------------------------------

# (code, type_attr, label, subcategory, pmc_prefix, include_re, exclude_re,
#  designation_ilike, max_d, max_l)


def _attr_defs(type_value: str) -> tuple[dict[str, Any], ...]:
    return (
        {
            "key": "type",
            "data_type": "enum",
            "required": True,
            "match_role": "identity",
            "enum_values": [type_value],
        },
        {
            "key": "diameter_mm",
            "data_type": "decimal",
            "required": True,
            "unit": "mm",
            "match_role": "identity",
        },
        {
            "key": "length_mm",
            "data_type": "int",
            "required": True,
            "unit": "mm",
            "match_role": "identity",
        },
        {
            "key": "packaging_qty",
            "data_type": "int",
            "required": False,
            "unit": "pièce",
            "match_role": "optional",
        },
    )


def _make_classifier(include: re.Pattern[str], exclude: re.Pattern[str]) -> Callable[[str], bool]:
    def _classify(designation: str) -> bool:
        blob = _fold(designation)
        if exclude.search(blob):
            return False
        return bool(include.search(blob))

    return _classify


def _register_fastener(
    *,
    code: str,
    type_value: str,
    label: str,
    subcategory: str,
    pmc_prefix: str,
    include: str,
    exclude: str,
    designation_ilike: tuple[str, ...],
    max_diameter_mm: str = "8",
    max_length_mm: int = 300,
) -> CategoryRule:
    include_re = re.compile(include, re.I)
    exclude_re = re.compile(exclude, re.I)
    classify = _make_classifier(include_re, exclude_re)
    extractor_version = f"{code.lower()}.v1"
    algorithm_version = f"{code.lower()}_match.v1"

    def _extract(designation: str, category_path: str | None = None) -> ExtractionResult:
        return extract_typed_fastener(
            designation,
            type_value=type_value,
            category_code=code,
            extractor_version=extractor_version,
            classify=classify,
            max_diameter_mm=Decimal(max_diameter_mm),
            max_length_mm=max_length_mm,
        )

    return register_rule(
        CategoryRule(
            code=code,
            category_name=label,
            identity=DIMENSIONAL_FASTENER,
            pmc_code_prefixes=(pmc_prefix,),
            pmc_subcategory_equals=(subcategory,),
            designation_ilike=designation_ilike,
            attribute_defs=_attr_defs(type_value),
            reference_unit_default="pièce",
            algorithm_version=algorithm_version,
            extractor_version=extractor_version,
            extract=_extract,
        )
    )


_COMMON_EXCLUDE = (
    r"visseuse|embout|assortiment|\bkits?\b|coffret|colle|mastic|"
    r"ni\s+clou\s+ni\s+vis|pistolet"
)

CHEVILLE_NYLON_RULE = _register_fastener(
    code="CHEVILLE_NYLON",
    type_value="cheville_nylon",
    label="Cheville nylon",
    subcategory="Chevilles nylon",
    pmc_prefix="PMC-CHEVILLE-NYLON-",
    include=r"chevil.*(?:nylon|plastique|plastic|duopower|universel|expansion)",
    exclude=(
        _COMMON_EXCLUDE
        + r"|molly|metal|metall|acier|laiton|a\s+frappe|autoforeuse|"
        r"avec\s+vis|\bvis\b"
    ),
    designation_ilike=("cheville", "nylon", "duopower"),
    max_diameter_mm="20",
    max_length_mm=200,
)

VIS_BETON_RULE = _register_fastener(
    code="VIS_BETON",
    type_value="beton",
    label="Vis béton",
    subcategory="Vis béton",
    pmc_prefix="PMC-VIS-BETON-",
    include=r"\bvis\b.*\bbeton\b|\bbeton\b.*\bvis\b",
    exclude=_COMMON_EXCLUDE + r"|cheville|boulon|tirefond|terrasse|\bagglo\b|\bplaco\b",
    designation_ilike=("vis béton", "vis beton", "vis à béton"),
    max_diameter_mm="12",
    max_length_mm=400,
)

VIS_TOLE_RULE = _register_fastener(
    code="VIS_TOLE",
    type_value="tole",
    label="Vis tôle",
    subcategory="Vis tôle",
    pmc_prefix="PMC-VIS-TOLE-",
    include=r"\bvis\b.*\btole\b|\btole\b.*\bvis\b|autoforeuse.*\bvis\b|\bvis\b.*autoforeuse",
    exclude=_COMMON_EXCLUDE + r"|cheville|beton|terrasse|agglo|placo|bois|multi",
    designation_ilike=("vis tôle", "vis tole", "autoforeuse"),
    max_diameter_mm="8",
    max_length_mm=200,
)

VIS_TERRASSE_RULE = _register_fastener(
    code="VIS_TERRASSE",
    type_value="terrasse",
    label="Vis terrasse",
    subcategory="Vis terrasse",
    pmc_prefix="PMC-VIS-TERRASSE-",
    include=r"\bvis\b.*terrasse|terrasse.*\bvis\b",
    exclude=_COMMON_EXCLUDE + r"|cheville|beton|tole|agglo|placo",
    designation_ilike=("vis terrasse", "vis de terrasse"),
    max_diameter_mm="10",
    max_length_mm=300,
)

TIREFOND_RULE = _register_fastener(
    code="TIREFOND",
    type_value="tirefond",
    label="Tirefond",
    subcategory="Tirefonds",
    pmc_prefix="PMC-TIREFOND-",
    include=r"tire[\s-]?fond|tirefond",
    exclude=_COMMON_EXCLUDE + r"|cheville",
    designation_ilike=("tirefond", "tire-fond", "tire fond"),
    max_diameter_mm="20",
    max_length_mm=400,
)

BOULON_RULE = _register_fastener(
    code="BOULON",
    type_value="boulon",
    label="Boulon",
    subcategory="Boulons",
    pmc_prefix="PMC-BOULON-",
    include=r"\bboulon\b|\btrcc\b|\bhb\b.*boulon|boulon.*poelier|boulon.*tete",
    exclude=_COMMON_EXCLUDE + r"|cheville|visseuse|\becrou\b\s*seul",
    designation_ilike=("boulon", "trcc"),
    max_diameter_mm="24",
    max_length_mm=400,
)

VIS_METAUX_RULE = _register_fastener(
    code="VIS_METAUX",
    type_value="metaux",
    label="Vis métaux",
    subcategory="Vis métaux",
    pmc_prefix="PMC-VIS-METAUX-",
    include=r"\bvis\b.*(?:metaux|mecanique)|(?:metaux|mecanique).*\bvis\b",
    exclude=(
        _COMMON_EXCLUDE
        + r"|cheville|terrasse|\btole\b|\bagglo\b|\bplaco\b|\bbois\b|\bbeton\b|"
        r"tirefond|boulon|goujon|charniere|bardage|\bcadre\b|multi|ecrou"
    ),
    designation_ilike=("métaux", "metaux", "mécanique", "mecanique"),
    max_diameter_mm="12",
    max_length_mm=200,
)

GOUJON_RULE = _register_fastener(
    code="GOUJON",
    type_value="goujon",
    label="Goujon d'ancrage",
    subcategory="Goujons",
    pmc_prefix="PMC-GOUJON-",
    include=r"goujon",
    exclude=_COMMON_EXCLUDE + r"|chemise|capsule",
    designation_ilike=("goujon",),
    max_diameter_mm="24",
    max_length_mm=400,
)

MASS_DIMENSIONAL_RULES = (
    CHEVILLE_NYLON_RULE,
    VIS_BETON_RULE,
    VIS_TOLE_RULE,
    VIS_TERRASSE_RULE,
    TIREFOND_RULE,
    BOULON_RULE,
    GOUJON_RULE,
    VIS_METAUX_RULE,
)

MASS_DIMENSIONAL_CODES = tuple(r.code for r in MASS_DIMENSIONAL_RULES)

MASS_DIMENSIONAL_META = {
    "CHEVILLE_NYLON": {
        "prefix": "PMC-CHEVILLE-NYLON-",
        "subcategory": "Chevilles nylon",
        "type": "cheville_nylon",
        "label": "Cheville nylon",
        "category": "Fixation",
    },
    "VIS_BETON": {
        "prefix": "PMC-VIS-BETON-",
        "subcategory": "Vis béton",
        "type": "beton",
        "label": "Vis béton",
        "category": "Fixation",
    },
    "VIS_TOLE": {
        "prefix": "PMC-VIS-TOLE-",
        "subcategory": "Vis tôle",
        "type": "tole",
        "label": "Vis tôle",
        "category": "Fixation",
    },
    "VIS_TERRASSE": {
        "prefix": "PMC-VIS-TERRASSE-",
        "subcategory": "Vis terrasse",
        "type": "terrasse",
        "label": "Vis terrasse",
        "category": "Fixation",
    },
    "TIREFOND": {
        "prefix": "PMC-TIREFOND-",
        "subcategory": "Tirefonds",
        "type": "tirefond",
        "label": "Tirefond",
        "category": "Fixation",
    },
    "BOULON": {
        "prefix": "PMC-BOULON-",
        "subcategory": "Boulons",
        "type": "boulon",
        "label": "Boulon",
        "category": "Fixation",
    },
    "GOUJON": {
        "prefix": "PMC-GOUJON-",
        "subcategory": "Goujons",
        "type": "goujon",
        "label": "Goujon d'ancrage",
        "category": "Fixation",
    },
    "VIS_METAUX": {
        "prefix": "PMC-VIS-METAUX-",
        "subcategory": "Vis métaux",
        "type": "metaux",
        "label": "Vis métaux",
        "category": "Fixation",
    },
}
