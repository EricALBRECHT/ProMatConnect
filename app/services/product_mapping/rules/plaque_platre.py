"""CategoryRule PLAQUE_PLATRE — identité L × l × Ép. + type.

Les flags fonctionnels (hydrofuge / feu / phonique) sont des compatibilités
optionnelles : un conflit dégrade le match, une absence ne le crée jamais.
"""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_PLAQUE_V1,
    CATEGORY_PLAQUE_PLATRE,
    EXTRACTOR_VERSION_PLAQUE_V1,
)
from app.services.product_mapping.rules.base import (
    PARTIAL_DIMS,
    CategoryRule,
    register_rule,
)

IDENTITY_KEYS = ("length_mm", "width_mm", "thickness_mm", "type")
BOOL_COMPAT_KEYS = ("hydrofuge", "fire_resistant", "acoustic")
DIM_KEYS = ("length_mm", "width_mm", "thickness_mm")

PLAQUE_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": [
            "standard",
            "hydrofuge",
            "multifonctions",
            "legere",
            "feu",
            "phonique",
        ],
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
    {
        "key": "hydrofuge",
        "data_type": "bool",
        "required": False,
        "match_role": "optional",
    },
    {
        "key": "fire_resistant",
        "data_type": "bool",
        "required": False,
        "match_role": "optional",
    },
    {
        "key": "acoustic",
        "data_type": "bool",
        "required": False,
        "match_role": "optional",
    },
]


def product_identity(
    code: str = "", attrs: dict | None = None, subcategory: str | None = None
) -> dict[str, Any]:
    """Projection PMC → clés d'identité plaque + flags dérivés du type."""
    source = dict(attrs or {})
    out: dict[str, Any] = {
        "length_mm": source.get("length_mm"),
        "width_mm": source.get("width_mm"),
        "thickness_mm": source.get("thickness_mm"),
        "type": source.get("type"),
        "hydrofuge": None,
        "fire_resistant": None,
        "acoustic": None,
    }
    ptype = out["type"]
    if ptype == "hydrofuge":
        out["hydrofuge"] = True
    elif ptype == "feu":
        out["fire_resistant"] = True
    elif ptype == "phonique":
        out["acoustic"] = True
    for key in BOOL_COMPAT_KEYS:
        if key in source and source[key] is not None:
            out[key] = bool(source[key])
    return out


def _extract(designation: str, category_path: str | None = None):
    # Import tardif : l'extracteur importe les primitives, pas la règle.
    from app.services.product_mapping.plaque_extractor import extract_plaque_platre

    return extract_plaque_platre(designation=designation, category_path=category_path)


PLAQUE_PLATRE_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_PLAQUE_PLATRE,
        category_name="Plaques de plâtre",
        identity_keys=IDENTITY_KEYS,
        product_key_map={
            "length_mm": "length_mm",
            "width_mm": "width_mm",
            "thickness_mm": "thickness_mm",
            "type": "type",
        },
        normalizations=(),
        pmc_code_prefixes=("PMC-BA",),
        pmc_subcategory_equals=("Plaques de plâtre",),
        designation_ilike=(
            "plaque",
            "BA13",
            "BA18",
            "BA10",
            "BA15",
            "BA25",
            "plâtre",
            "platre",
            "Purelight",
            "placo",
        ),
        allow_high=True,
        partial_strategy=PARTIAL_DIMS,
        optional_compat_keys=BOOL_COMPAT_KEYS,
        dim_keys_for_partial=DIM_KEYS,
        high_type_key="type",
        attribute_defs=tuple(PLAQUE_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_PLAQUE_V1,
        extractor_version=EXTRACTOR_VERSION_PLAQUE_V1,
        extract=_extract,
        enrich_product_attrs=product_identity,
    )
)
