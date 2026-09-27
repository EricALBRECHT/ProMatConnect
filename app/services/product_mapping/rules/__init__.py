"""Registre des CategoryRule — une famille produit = une configuration."""

from app.services.product_mapping.rules.base import (
    CategoryRule,
    NormalizationSpec,
    apply_normalization_specs,
    get_rule,
    register_rule,
    registered_codes,
)
from app.services.product_mapping.rules.ossature_placo import OSSATURE_PLACO_RULE
from app.services.product_mapping.rules.plaque_platre import PLAQUE_PLATRE_RULE
from app.services.product_mapping.rules.vis_placo import VIS_PLACO_RULE

__all__ = [
    "CategoryRule",
    "NormalizationSpec",
    "OSSATURE_PLACO_RULE",
    "PLAQUE_PLATRE_RULE",
    "VIS_PLACO_RULE",
    "apply_normalization_specs",
    "get_rule",
    "register_rule",
    "registered_codes",
]
