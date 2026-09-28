"""Registre des CategoryRule — une famille produit = une configuration."""

from app.services.product_mapping.identity import IdentityModel
from app.services.product_mapping.identity_models import (
    BOARD_PANEL,
    DIMENSIONAL_FASTENER,
    LINEAR_PROFILE,
)
from app.services.product_mapping.rules.base import (
    CategoryRule,
    NormalizationSpec,
    apply_normalization_specs,
    get_rule,
    register_rule,
    registered_codes,
)
from app.services.product_mapping.rules.cheville_metal import CHEVILLE_METAL_RULE
from app.services.product_mapping.rules.ossature_placo import OSSATURE_PLACO_RULE
from app.services.product_mapping.rules.plaque_platre import PLAQUE_PLATRE_RULE
from app.services.product_mapping.rules.vis_agglo import VIS_AGGLO_RULE
from app.services.product_mapping.rules.vis_bois import VIS_BOIS_RULE
from app.services.product_mapping.rules.vis_multi import VIS_MULTI_RULE
from app.services.product_mapping.rules.vis_placo import VIS_PLACO_RULE

__all__ = [
    "BOARD_PANEL",
    "CHEVILLE_METAL_RULE",
    "CategoryRule",
    "DIMENSIONAL_FASTENER",
    "IdentityModel",
    "LINEAR_PROFILE",
    "NormalizationSpec",
    "OSSATURE_PLACO_RULE",
    "PLAQUE_PLATRE_RULE",
    "VIS_AGGLO_RULE",
    "VIS_BOIS_RULE",
    "VIS_MULTI_RULE",
    "VIS_PLACO_RULE",
    "apply_normalization_specs",
    "get_rule",
    "register_rule",
    "registered_codes",
]
