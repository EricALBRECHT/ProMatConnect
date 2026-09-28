"""Modèles d'identité justifiés par les catégories V2 déjà validées.

Pas de LIQUID_FINISH / PIPE ici — réservés à une extension future une fois
la politique d'identité arrêtée (ex. peinture).
"""

from __future__ import annotations

from app.services.product_mapping.identity import (
    PARTIAL_DIMS,
    PARTIAL_HIERARCHY,
    IdentityModel,
    NormalizationSpec,
)

# ---------------------------------------------------------------------------
# DIMENSIONAL_FASTENER — vis / futures fixations Ø × L
# ---------------------------------------------------------------------------

DIMENSIONAL_FASTENER = IdentityModel(
    name="DIMENSIONAL_FASTENER",
    identity_keys=("type", "diameter_mm", "length_mm"),
    product_key_map={
        "type": "type",
        "diameter_mm": "diameter_mm",
        "length_mm": "length_mm",
    },
    normalizations=(),
    ignored_for_identity=("packaging_qty", "head", "drive", "finish"),
    allow_high=False,
    partial_strategy=PARTIAL_HIERARCHY,
    hierarchy_keys=("type", "diameter_mm"),
)

# ---------------------------------------------------------------------------
# LINEAR_PROFILE — rails / montants / fourrures
# ---------------------------------------------------------------------------

# Table EXPLICITE (pas une tolérance ±). 2480/2510/2980/3010 restent inchangés.
NOMINAL_LENGTH_MAP: dict[int, int] = {
    2490: 2500,
    2500: 2500,
    2990: 3000,
    3000: 3000,
}

NOMINAL_LENGTH_SPEC = NormalizationSpec(
    source_key="length_mm",
    target_key="nominal_length_mm",
    mapping=NOMINAL_LENGTH_MAP,
)

LINEAR_PROFILE = IdentityModel(
    name="LINEAR_PROFILE",
    identity_keys=("kind", "profile", "nominal_length_mm"),
    product_key_map={
        "kind": "kind",
        "profile": "profile",
        "nominal_length_mm": "length_mm",
    },
    normalizations=(NOMINAL_LENGTH_SPEC,),
    ignored_for_identity=("packaging_qty",),
    allow_high=False,
    partial_strategy=PARTIAL_HIERARCHY,
    hierarchy_keys=("kind", "profile"),
)

# ---------------------------------------------------------------------------
# BOARD_PANEL — plaques de plâtre (futurs OSB / panneaux possibles)
# ---------------------------------------------------------------------------

BOARD_PANEL_BOOL_COMPAT = ("hydrofuge", "fire_resistant", "acoustic")
BOARD_PANEL_DIM_KEYS = ("length_mm", "width_mm", "thickness_mm")

BOARD_PANEL = IdentityModel(
    name="BOARD_PANEL",
    identity_keys=("length_mm", "width_mm", "thickness_mm", "type"),
    product_key_map={
        "length_mm": "length_mm",
        "width_mm": "width_mm",
        "thickness_mm": "thickness_mm",
        "type": "type",
    },
    normalizations=(),
    ignored_for_identity=(),
    allow_high=True,
    partial_strategy=PARTIAL_DIMS,
    optional_compat_keys=BOARD_PANEL_BOOL_COMPAT,
    dim_keys_for_partial=BOARD_PANEL_DIM_KEYS,
    high_type_key="type",
)

# ---------------------------------------------------------------------------
# BAGGED_PRODUCT — produit en sac dont le poids est la variante commerciale
# ---------------------------------------------------------------------------

BAGGED_PRODUCT = IdentityModel(
    name="BAGGED_PRODUCT",
    identity_keys=("type", "weight_kg"),
    product_key_map={"type": "type", "weight_kg": "weight_kg"},
    normalizations=(),
    ignored_for_identity=(),
    allow_high=False,
    partial_strategy=PARTIAL_HIERARCHY,
    hierarchy_keys=("type",),
)

# ---------------------------------------------------------------------------
# ELECTRICAL_CABLE — norme + section + conducteurs + longueur + couleur
# ---------------------------------------------------------------------------

ELECTRICAL_CABLE = IdentityModel(
    name="ELECTRICAL_CABLE",
    identity_keys=("kind", "section_mm2", "conductors", "length_m", "color"),
    product_key_map={
        "kind": "kind",
        "section_mm2": "section_mm2",
        "conductors": "conductors",
        "length_m": "length_m",
        "color": "color",
    },
    normalizations=(),
    ignored_for_identity=(),
    allow_high=False,
    partial_strategy=PARTIAL_HIERARCHY,
    hierarchy_keys=("kind", "section_mm2", "conductors"),
)

__all__ = [
    "BAGGED_PRODUCT",
    "BOARD_PANEL",
    "BOARD_PANEL_BOOL_COMPAT",
    "BOARD_PANEL_DIM_KEYS",
    "DIMENSIONAL_FASTENER",
    "ELECTRICAL_CABLE",
    "LINEAR_PROFILE",
    "NOMINAL_LENGTH_MAP",
    "NOMINAL_LENGTH_SPEC",
]
