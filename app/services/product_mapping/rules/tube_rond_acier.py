"""CategoryRule TUBE_ROND_ACIER — LINEAR_PROFILE."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_TUBE_ROND_ACIER_V1,
    CATEGORY_TUBE_ROND_ACIER,
    EXTRACTOR_VERSION_TUBE_ROND_ACIER_V1,
    KIND_TUBE_ROND_ACIER,
)
from app.services.product_mapping.identity_models import LINEAR_PROFILE
from app.services.product_mapping.rules.base import CategoryRule, register_rule

ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "kind",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": [KIND_TUBE_ROND_ACIER],
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
    from app.services.product_mapping.tube_rond_acier_extractor import (
        extract_tube_rond_acier,
    )

    return extract_tube_rond_acier(designation, category_path)


def _enrich(code: str, attrs: dict | None, subcategory: str | None) -> dict:
    source = dict(attrs or {})
    source.setdefault("kind", KIND_TUBE_ROND_ACIER)
    return source


TUBE_ROND_ACIER_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_TUBE_ROND_ACIER,
        category_name="Tube rond acier",
        identity=LINEAR_PROFILE,
        pmc_code_prefixes=("PMC-TUBE-ROND-ACIER-",),
        pmc_subcategory_equals=("Tube rond acier",),
        designation_ilike=("tube rond acier",),
        attribute_defs=tuple(ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_TUBE_ROND_ACIER_V1,
        extractor_version=EXTRACTOR_VERSION_TUBE_ROND_ACIER_V1,
        extract=_extract,
        enrich_product_attrs=_enrich,
    )
)
