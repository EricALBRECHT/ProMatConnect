"""CategoryRule VIS_AGGLO — identité type + diamètre + longueur (type=agglo)."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_VIS_AGGLO_V1,
    CATEGORY_VIS_AGGLO,
    EXTRACTOR_VERSION_VIS_AGGLO_V1,
)
from app.services.product_mapping.rules.base import (
    PARTIAL_HIERARCHY,
    CategoryRule,
    register_rule,
)

IDENTITY_KEYS = ("type", "diameter_mm", "length_mm")

VIS_AGGLO_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": ["agglo"],
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
    {
        "key": "head",
        "data_type": "text",
        "required": False,
        "match_role": "optional",
    },
    {
        "key": "drive",
        "data_type": "text",
        "required": False,
        "match_role": "optional",
    },
    {
        "key": "finish",
        "data_type": "text",
        "required": False,
        "match_role": "optional",
    },
]


def _extract(designation: str, category_path: str | None = None):
    from app.services.product_mapping.vis_agglo_extractor import extract_vis_agglo

    return extract_vis_agglo(designation, category_path)


VIS_AGGLO_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_VIS_AGGLO,
        category_name="Vis agglo",
        identity_keys=IDENTITY_KEYS,
        product_key_map={
            "type": "type",
            "diameter_mm": "diameter_mm",
            "length_mm": "length_mm",
        },
        normalizations=(),
        pmc_code_prefixes=("PMC-VIS-AGGLO-",),
        pmc_subcategory_equals=("Vis agglo",),
        designation_ilike=(
            "vis agglo",
            "agglo",
        ),
        allow_high=False,
        partial_strategy=PARTIAL_HIERARCHY,
        hierarchy_keys=("type", "diameter_mm"),
        attribute_defs=tuple(VIS_AGGLO_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_VIS_AGGLO_V1,
        extractor_version=EXTRACTOR_VERSION_VIS_AGGLO_V1,
        extract=_extract,
    )
)
