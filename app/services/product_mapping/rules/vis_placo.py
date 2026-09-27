"""CategoryRule VIS_PLACO — identité type + diamètre + longueur.

Le diamètre est exprimé en millimètres réels (3.5), comme les attributs PMC —
pas en dixièmes. Aucune table de normalisation : les désignations fournisseur
écrivent toutes le diamètre commercial tel quel (3,5 / 4,2 / 4,8).

Le conditionnement (200 pièces, seau de 1000, boîte de 2 kg) est un attribut
optionnel : deux conditionnements d'une même vis sont le même produit.
"""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_VIS_V1,
    CATEGORY_VIS_PLACO,
    EXTRACTOR_VERSION_VIS_V1,
)
from app.services.product_mapping.rules.base import (
    PARTIAL_HIERARCHY,
    CategoryRule,
    register_rule,
)

IDENTITY_KEYS = ("type", "diameter_mm", "length_mm")

VIS_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": ["placo"],
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
    # Import tardif : l'extracteur importe les primitives, pas la règle.
    from app.services.product_mapping.vis_extractor import extract_vis_placo

    return extract_vis_placo(designation, category_path)


VIS_PLACO_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_VIS_PLACO,
        category_name="Vis placo",
        identity_keys=IDENTITY_KEYS,
        product_key_map={
            "type": "type",
            "diameter_mm": "diameter_mm",
            "length_mm": "length_mm",
        },
        normalizations=(),
        pmc_code_prefixes=("PMC-VIS-PLACO-",),
        pmc_subcategory_equals=("Vis placo",),
        designation_ilike=(
            "vis plaque",
            "vis pour plaque",
            "vis placo",
            "plaq platre",
            "trompette",
            "ttpc",
            "autoperçante",
            "autopercante",
            "autoperforante",
            "autoforeuse",
        ),
        allow_high=False,
        partial_strategy=PARTIAL_HIERARCHY,
        hierarchy_keys=("type", "diameter_mm"),
        attribute_defs=tuple(VIS_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_VIS_V1,
        extractor_version=EXTRACTOR_VERSION_VIS_V1,
        extract=_extract,
    )
)
