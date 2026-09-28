"""Familles BOARD_PANEL à fort volume (OSB / CP / aggloméré) — enregistrement compact."""

from __future__ import annotations

import re
from typing import Any, Callable

from app.services.product_mapping.identity_models import BOARD_PANEL
from app.services.product_mapping.panneau_mdf_extractor import ExtractionResult
from app.services.product_mapping.primitives.dimensions import (
    extract_dimensions_mm,
    extract_length_width_mm,
    extract_thickness_alone_mm,
)
from app.services.product_mapping.primitives.textutil import fold as _fold
from app.services.product_mapping.rules.base import CategoryRule, register_rule

REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"

_EXCLUDE = re.compile(
    r"equerre|\blots?\b|\bkits?\b|porte|cloison|treillis|brise|cloture|"
    r"plinthe|lambris|chant\b|meuble|corniere|vis\b|cheville",
    re.I,
)


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
            "key": "length_mm",
            "data_type": "int",
            "required": True,
            "unit": "mm",
            "match_role": "identity",
        },
        {
            "key": "width_mm",
            "data_type": "int",
            "required": True,
            "unit": "mm",
            "match_role": "identity",
        },
        {
            "key": "thickness_mm",
            "data_type": "int",
            "required": True,
            "unit": "mm",
            "match_role": "identity",
        },
    )


def _extract_dims(designation: str) -> tuple[int | None, int | None, int | None]:
    cleaned = re.sub(r"(\d)\s+(\d{3})\b", r"\1\2", designation)
    length_mm, width_mm, thickness_mm = extract_dimensions_mm(cleaned)
    if length_mm is None and width_mm is None and thickness_mm is None:
        length_mm, width_mm = extract_length_width_mm(cleaned)
        thickness_mm = extract_thickness_alone_mm(cleaned)
    if length_mm is not None and width_mm is not None and width_mm > length_mm:
        length_mm, width_mm = width_mm, length_mm
    # Panneaux : bornes raisonnables
    if length_mm is not None and not (200 <= length_mm <= 4000):
        length_mm = None
    if width_mm is not None and not (200 <= width_mm <= 2000):
        width_mm = None
    if thickness_mm is not None and not (3 <= thickness_mm <= 40):
        thickness_mm = None
    return length_mm, width_mm, thickness_mm


def _register_panel(
    *,
    code: str,
    type_value: str,
    label: str,
    subcategory: str,
    pmc_prefix: str,
    include: str,
    designation_ilike: tuple[str, ...],
) -> CategoryRule:
    include_re = re.compile(include, re.I)

    def classify(designation: str) -> bool:
        blob = _fold(designation)
        if _EXCLUDE.search(blob):
            return False
        if "panneau" not in blob and "osb" not in blob:
            return False
        return bool(include_re.search(blob))

    extractor_version = f"{code.lower()}.v1"
    algorithm_version = f"{code.lower()}_match.v1"

    def _extract(designation: str, category_path: str | None = None) -> ExtractionResult:
        if not classify(designation):
            return ExtractionResult(
                category_code=None,
                attributes={},
                confidence=0.0,
                extractor_version=extractor_version,
                classified=False,
                reason=REASON_NOT_THIS_CATEGORY,
            )
        L, W, T = _extract_dims(designation)
        attrs = {
            "type": type_value,
            "length_mm": L,
            "width_mm": W,
            "thickness_mm": T,
        }
        conf = 0.4 + 0.2 * sum(1 for v in (L, W, T) if v is not None)
        return ExtractionResult(
            category_code=code,
            attributes=attrs,
            confidence=conf,
            extractor_version=extractor_version,
            classified=True,
            reason=None,
        )

    return register_rule(
        CategoryRule(
            code=code,
            category_name=label,
            identity=BOARD_PANEL,
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


PANNEAU_OSB_RULE = _register_panel(
    code="PANNEAU_OSB",
    type_value="osb",
    label="Panneau OSB",
    subcategory="Panneau OSB",
    pmc_prefix="PMC-PANNEAU-OSB-",
    include=r"\bosb\b",
    designation_ilike=("osb", "panneau osb"),
)

PANNEAU_CP_RULE = _register_panel(
    code="PANNEAU_CP",
    type_value="contreplaque",
    label="Panneau contreplaqué",
    subcategory="Panneau contreplaqué",
    pmc_prefix="PMC-PANNEAU-CP-",
    include=r"contreplaqu|contre[\s-]?plaque|\bcp\b.*panneau|panneau.*\bcp\b",
    designation_ilike=("contreplaqué", "contreplaque", "panneau cp"),
)

PANNEAU_AGGLO_RULE = _register_panel(
    code="PANNEAU_AGGLO",
    type_value="agglomere",
    label="Panneau aggloméré",
    subcategory="Panneau aggloméré",
    pmc_prefix="PMC-PANNEAU-AGGLO-",
    include=r"agglomer|agglo(?!.*\bvis\b)|panneau.*particule|particule",
    designation_ilike=("aggloméré", "agglomere", "panneau particules"),
)

MASS_PANEL_RULES = (PANNEAU_OSB_RULE, PANNEAU_CP_RULE, PANNEAU_AGGLO_RULE)
MASS_PANEL_CODES = tuple(r.code for r in MASS_PANEL_RULES)

MASS_PANEL_META = {
    "PANNEAU_OSB": {
        "prefix": "PMC-PANNEAU-OSB-",
        "subcategory": "Panneau OSB",
        "type": "osb",
        "label": "Panneau OSB",
        "category": "Panneau",
    },
    "PANNEAU_CP": {
        "prefix": "PMC-PANNEAU-CP-",
        "subcategory": "Panneau contreplaqué",
        "type": "contreplaque",
        "label": "Panneau contreplaqué",
        "category": "Panneau",
    },
    "PANNEAU_AGGLO": {
        "prefix": "PMC-PANNEAU-AGGLO-",
        "subcategory": "Panneau aggloméré",
        "type": "agglomere",
        "label": "Panneau aggloméré",
        "category": "Panneau",
    },
}
