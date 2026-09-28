"""CategoryRule PANNEAU_MDF — BOARD_PANEL type=mdf."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_PANNEAU_MDF_V1,
    CATEGORY_PANNEAU_MDF,
    EXTRACTOR_VERSION_PANNEAU_MDF_V1,
)
from app.services.product_mapping.identity_models import BOARD_PANEL
from app.services.product_mapping.rules.base import CategoryRule, register_rule

ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": ["mdf"],
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
]


def _extract(designation: str, category_path: str | None = None):
    from app.services.product_mapping.panneau_mdf_extractor import extract_panneau_mdf

    return extract_panneau_mdf(designation, category_path)


def _enrich(code: str, attrs: dict | None, subcategory: str | None) -> dict:
    source = dict(attrs or {})
    source.setdefault("type", "mdf")
    return source


PANNEAU_MDF_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_PANNEAU_MDF,
        category_name="Panneau MDF",
        identity=BOARD_PANEL,
        pmc_code_prefixes=("PMC-PANNEAU-MDF-",),
        pmc_subcategory_equals=("Panneau MDF",),
        designation_ilike=("panneau bois mdf", "panneau mdf", "mdf"),
        attribute_defs=tuple(ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_PANNEAU_MDF_V1,
        extractor_version=EXTRACTOR_VERSION_PANNEAU_MDF_V1,
        extract=_extract,
        enrich_product_attrs=_enrich,
    )
)
