"""CategoryRule FER_BETON — LINEAR_PROFILE."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_FER_BETON_V1,
    CATEGORY_FER_BETON,
    EXTRACTOR_VERSION_FER_BETON_V1,
    KIND_FER_BETON,
)
from app.services.product_mapping.identity_models import LINEAR_PROFILE
from app.services.product_mapping.rules.base import CategoryRule, register_rule

ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "kind",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": [KIND_FER_BETON],
    },
    {"key": "profile", "data_type": "text", "required": True, "match_role": "identity"},
    {
        "key": "length_mm",
        "data_type": "int",
        "required": True,
        "unit": "mm",
        "match_role": "identity",
    },
]


def _extract(designation: str, category_path: str | None = None):
    from app.services.product_mapping.fer_beton_extractor import extract_fer_beton

    return extract_fer_beton(designation, category_path)


def _enrich(code: str, attrs: dict | None, subcategory: str | None) -> dict:
    source = dict(attrs or {})
    source.setdefault("kind", KIND_FER_BETON)
    return source


FER_BETON_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_FER_BETON,
        category_name="Fer à béton",
        identity=LINEAR_PROFILE,
        pmc_code_prefixes=("PMC-FER-BETON-",),
        pmc_subcategory_equals=("Fer à béton",),
        designation_ilike=("fer à béton", "fer a beton", "torsadé"),
        attribute_defs=tuple(ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_FER_BETON_V1,
        extractor_version=EXTRACTOR_VERSION_FER_BETON_V1,
        extract=_extract,
        enrich_product_attrs=_enrich,
    )
)
