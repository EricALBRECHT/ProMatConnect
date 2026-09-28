"""CategoryRule VIS_MULTI — DIMENSIONAL_FASTENER (type=multi)."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_VIS_MULTI_V1,
    CATEGORY_VIS_MULTI,
    EXTRACTOR_VERSION_VIS_MULTI_V1,
)
from app.services.product_mapping.identity_models import DIMENSIONAL_FASTENER
from app.services.product_mapping.rules.base import CategoryRule, register_rule

IDENTITY_KEYS = DIMENSIONAL_FASTENER.identity_keys

VIS_MULTI_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": ["multi"],
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
    {"key": "head", "data_type": "text", "required": False, "match_role": "optional"},
    {"key": "drive", "data_type": "text", "required": False, "match_role": "optional"},
    {"key": "finish", "data_type": "text", "required": False, "match_role": "optional"},
]


def _extract(designation: str, category_path: str | None = None):
    from app.services.product_mapping.vis_multi_extractor import extract_vis_multi

    return extract_vis_multi(designation, category_path)


VIS_MULTI_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_VIS_MULTI,
        category_name="Vis multi-matériaux",
        identity=DIMENSIONAL_FASTENER,
        pmc_code_prefixes=("PMC-VIS-MULTI-",),
        pmc_subcategory_equals=("Vis multi",),
        designation_ilike=(
            "multi-matériaux",
            "multi-materiaux",
            "multi materiaux",
            "multi materiau",
            "tous matériaux",
            "tous materiaux",
            "tous-materiaux",
            "multi-supports",
            "multi-support",
            "multi support",
            "multi-usage",
            "multi usage",
            "multiusage",
        ),
        attribute_defs=tuple(VIS_MULTI_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_VIS_MULTI_V1,
        extractor_version=EXTRACTOR_VERSION_VIS_MULTI_V1,
        extract=_extract,
    )
)
