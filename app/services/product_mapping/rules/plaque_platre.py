"""CategoryRule PLAQUE_PLATRE — IdentityModel BOARD_PANEL.

Les flags fonctionnels (hydrofuge / feu / phonique) restent des compatibilités
optionnelles portées par le modèle ; enrich_product_attrs reste spécifique.
"""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_PLAQUE_V1,
    CATEGORY_PLAQUE_PLATRE,
    EXTRACTOR_VERSION_PLAQUE_V1,
)
from app.services.product_mapping.identity_models import (
    BOARD_PANEL,
    BOARD_PANEL_BOOL_COMPAT,
    BOARD_PANEL_DIM_KEYS,
)
from app.services.product_mapping.rules.base import CategoryRule, register_rule

IDENTITY_KEYS = BOARD_PANEL.identity_keys
BOOL_COMPAT_KEYS = BOARD_PANEL_BOOL_COMPAT
DIM_KEYS = BOARD_PANEL_DIM_KEYS

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
    from app.services.product_mapping.plaque_extractor import extract_plaque_platre

    return extract_plaque_platre(designation=designation, category_path=category_path)


PLAQUE_PLATRE_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_PLAQUE_PLATRE,
        category_name="Plaques de plâtre",
        identity=BOARD_PANEL,
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
        attribute_defs=tuple(PLAQUE_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_PLAQUE_V1,
        extractor_version=EXTRACTOR_VERSION_PLAQUE_V1,
        extract=_extract,
        enrich_product_attrs=product_identity,
    )
)
