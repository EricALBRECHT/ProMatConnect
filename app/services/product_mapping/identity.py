"""IdentityModel — modèle d'identité produit réutilisable entre CategoryRule.

Séparation stricte :
  SupplierProduct → extracteurs/primitives → FeatureSet → normalisations
  → IdentityModel (clés d'identité + stratégie de match) → generic_matcher

IdentityModel ne parse pas de texte. Il déclare seulement *quoi* matcher et
comment (normalisations explicites, repli partiel). Une nouvelle famille proche
= classifier/extracteur léger + choix d'un modèle + paramètres CategoryRule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.services.product_mapping.rules.base import (
    PARTIAL_DIMS,
    PARTIAL_HIERARCHY,
    NormalizationSpec,
    apply_normalization_specs,
)


@dataclass(frozen=True)
class IdentityModel:
    """Contrat d'identité PMC partagé — pas un extracteur, pas un matcher."""

    name: str
    identity_keys: tuple[str, ...]
    product_key_map: Mapping[str, str] = field(default_factory=dict)
    normalizations: tuple[NormalizationSpec, ...] = ()
    # Attributs extraits utiles (conditionnement, finition…) mais hors identité.
    ignored_for_identity: tuple[str, ...] = ()

    # Stratégie de match liée au *type* d'identité (pas à une famille métier).
    allow_high: bool = False
    partial_strategy: str = PARTIAL_DIMS
    optional_compat_keys: tuple[str, ...] = ()
    dim_keys_for_partial: tuple[str, ...] = ()
    high_type_key: str | None = None
    hierarchy_keys: tuple[str, ...] = ()

    def product_key(self, extracted_key: str) -> str:
        return self.product_key_map.get(extracted_key, extracted_key)

    def identity_complete(self, extracted: Mapping[str, Any]) -> bool:
        return all(extracted.get(k) is not None for k in self.identity_keys)

    def normalize(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return apply_normalization_specs(attrs, self.normalizations)

    def identity_view(self, extracted: Mapping[str, Any]) -> dict[str, Any]:
        """Sous-ensemble d'attributs d'identité (ignore packaging / secondaires)."""
        return {k: extracted.get(k) for k in self.identity_keys}


# Réexport local pour les définitions de modèles (évite cycles d'import).
__all__ = [
    "IdentityModel",
    "PARTIAL_DIMS",
    "PARTIAL_HIERARCHY",
    "NormalizationSpec",
]
