"""CategoryRule CORNIERE_PVC — LINEAR_PROFILE."""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_CORNIERE_PVC_V1,
    CATEGORY_CORNIERE_PVC,
    EXTRACTOR_VERSION_CORNIERE_PVC_V1,
    KIND_CORNIERE_PVC,
)
from app.services.product_mapping.identity_models import LINEAR_PROFILE
from app.services.product_mapping.rules.base import CategoryRule, register_rule

ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "kind",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": [KIND_CORNIERE_PVC],
    },
    {
        "key": "profile",
        "data_type": "text",
        "required": True,
        "match_role": "identity",
    },
    {
        "key": "length_mm",
        "data_type": "int",
        "required": True,
        "unit": "mm",
        "match_role": "identity",
    },
]


def _extract(designation: str, category_path: str | None = None):
    from app.services.product_mapping.corniere_pvc_extractor import extract_corniere_pvc

    return extract_corniere_pvc(designation, category_path)


def _enrich(code: str, attrs: dict | None, subcategory: str | None) -> dict:
    source = dict(attrs or {})
    if not source.get("kind"):
        source["kind"] = KIND_CORNIERE_PVC
    return source


CORNIERE_PVC_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_CORNIERE_PVC,
        category_name="Cornière PVC",
        identity=LINEAR_PROFILE,
        pmc_code_prefixes=("PMC-CORNIERE-PVC-",),
        pmc_subcategory_equals=("Cornière PVC",),
        designation_ilike=("cornière pvc", "corniere pvc", "cornière en pvc", "corniere en pvc"),
        attribute_defs=tuple(ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_CORNIERE_PVC_V1,
        extractor_version=EXTRACTOR_VERSION_CORNIERE_PVC_V1,
        extract=_extract,
        enrich_product_attrs=_enrich,
    )
)
