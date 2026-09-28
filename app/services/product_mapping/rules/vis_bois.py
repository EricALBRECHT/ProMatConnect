"""CategoryRule VIS_BOIS — DIMENSIONAL_FASTENER (type=bois)."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_VIS_BOIS_V1,
    CATEGORY_VIS_BOIS,
    EXTRACTOR_VERSION_VIS_BOIS_V1,
)
from app.services.product_mapping.identity_models import DIMENSIONAL_FASTENER
from app.services.product_mapping.rules.base import CategoryRule, register_rule

IDENTITY_KEYS = DIMENSIONAL_FASTENER.identity_keys

VIS_BOIS_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": ["bois"],
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
    from app.services.product_mapping.vis_bois_extractor import extract_vis_bois

    return extract_vis_bois(designation, category_path)


VIS_BOIS_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_VIS_BOIS,
        category_name="Vis bois",
        identity=DIMENSIONAL_FASTENER,
        pmc_code_prefixes=("PMC-VIS-BOIS-",),
        pmc_subcategory_equals=("Vis bois",),
        designation_ilike=("vis bois", "vis à bois", "vis a bois"),
        attribute_defs=tuple(VIS_BOIS_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_VIS_BOIS_V1,
        extractor_version=EXTRACTOR_VERSION_VIS_BOIS_V1,
        extract=_extract,
    )
)
