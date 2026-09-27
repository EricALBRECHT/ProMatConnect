"""Exemple documentaire : une catégorie VIS_PLACO ajoutée en configuration seule.

NON enregistrée dans le registre — aucun run réel ne l'utilise. Elle illustre le
coût d'ajout d'une famille en V2 : une CategoryRule + un extracteur assemblant
des primitives existantes, sans toucher au matcher ni au pipeline.
"""

from __future__ import annotations

from app.services.product_mapping.feature_set import FeatureSet, features_from_attrs
from app.services.product_mapping.primitives import (
    extract_lot_quantity,
    extract_metal_profile,
)
from app.services.product_mapping.primitives.dimensions import (
    extract_thickness_alone_mm,
)
from app.services.product_mapping.rules.base import (
    PARTIAL_HIERARCHY,
    CategoryRule,
    NormalizationSpec,
)

# Diamètres commerciaux : 3,5 mm facturé tantôt 35 (dixièmes) tantôt 4 (arrondi).
NOMINAL_DIAMETER_MAP = {35: 35, 4: 35, 39: 39, 42: 42}


def extract_vis_placo(designation: str, category_path: str | None = None) -> FeatureSet:
    attrs = {
        "kind": "VIS" if "vis" in designation.lower() else None,
        "head": extract_metal_profile(designation),
        "length_mm": extract_thickness_alone_mm(designation),
        "lot_quantity": extract_lot_quantity(designation),
    }
    return FeatureSet(
        category_code="VIS_PLACO",
        values=features_from_attrs(attrs),
        classified=attrs["kind"] is not None,
        confidence=0.6,
        extractor_version="vis_placo.v1",
    )


VIS_PLACO_RULE_EXAMPLE = CategoryRule(
    code="VIS_PLACO",
    category_name="Vis placo",
    identity_keys=("kind", "head", "nominal_diameter_tenths"),
    product_key_map={
        "kind": "kind",
        "head": "head",
        "nominal_diameter_tenths": "diameter_tenths",
    },
    normalizations=(
        NormalizationSpec(
            source_key="diameter_tenths",
            target_key="nominal_diameter_tenths",
            mapping=NOMINAL_DIAMETER_MAP,
        ),
    ),
    pmc_code_prefixes=("PMC-VIS-",),
    pmc_subcategory_equals=("Visserie placo",),
    designation_ilike=("vis placo", "vis plaque", "vis autoperforante"),
    partial_strategy=PARTIAL_HIERARCHY,
    hierarchy_keys=("kind", "head"),
    attribute_defs=(
        {"key": "kind", "data_type": "enum", "required": True, "match_role": "identity"},
        {"key": "head", "data_type": "enum", "required": True, "match_role": "identity"},
        {
            "key": "diameter_tenths",
            "data_type": "int",
            "required": True,
            "match_role": "identity",
        },
        {"key": "lot_quantity", "data_type": "int", "match_role": "optional"},
    ),
    extract=extract_vis_placo,
)
