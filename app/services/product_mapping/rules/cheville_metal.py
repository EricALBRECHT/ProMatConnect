"""CategoryRule CHEVILLE_METAL — DIMENSIONAL_FASTENER (type=cheville_metal)."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_CHEVILLE_METAL_V1,
    CATEGORY_CHEVILLE_METAL,
    EXTRACTOR_VERSION_CHEVILLE_METAL_V1,
)
from app.services.product_mapping.identity_models import DIMENSIONAL_FASTENER
from app.services.product_mapping.rules.base import CategoryRule, register_rule

IDENTITY_KEYS = DIMENSIONAL_FASTENER.identity_keys

CHEVILLE_METAL_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": ["cheville_metal"],
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
]


def _extract(designation: str, category_path: str | None = None):
    from app.services.product_mapping.cheville_metal_extractor import (
        extract_cheville_metal,
    )

    return extract_cheville_metal(designation, category_path)


CHEVILLE_METAL_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_CHEVILLE_METAL,
        category_name="Chevilles métal",
        identity=DIMENSIONAL_FASTENER,
        pmc_code_prefixes=("PMC-CHEVILLE-METAL-",),
        pmc_subcategory_equals=("Chevilles métal",),
        designation_ilike=(
            "cheville",
            "molly",
        ),
        attribute_defs=tuple(CHEVILLE_METAL_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_CHEVILLE_METAL_V1,
        extractor_version=EXTRACTOR_VERSION_CHEVILLE_METAL_V1,
        extract=_extract,
    )
)
