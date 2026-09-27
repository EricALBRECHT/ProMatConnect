"""CategoryRule — description déclarative d'une famille produit.

Une catégorie = une configuration (clés d'identité, sélecteurs PMC, filtres SQL,
normalisations explicites) + deux callables métier (extraction, projection PMC).
Le moteur générique (generic_matcher / pipeline) ne connaît rien d'autre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

# Stratégies de repli quand l'identité n'est pas complètement satisfaite.
PARTIAL_DIMS = "dims"  # plaques : dimensions identiques → HIGH / REVIEW
PARTIAL_HIERARCHY = "hierarchy"  # ossature : kind → profile → longueur

ALGORITHM_VERSION_GENERIC_V2 = "generic_match.v2"
EXTRACTOR_VERSION_GENERIC_V2 = "generic_extract.v2"


@dataclass(frozen=True)
class NormalizationSpec:
    """Correspondance explicite source → cible (jamais un arrondi implicite).

    ``source_key`` n'est jamais modifiée : la valeur fournisseur reste traçable.
    Une valeur absente de ``mapping`` traverse inchangée vers ``target_key``.
    """

    source_key: str
    target_key: str
    mapping: Mapping[Any, Any] = field(default_factory=dict)


def apply_normalization_specs(
    attrs: dict[str, Any], specs: tuple[NormalizationSpec, ...]
) -> dict[str, Any]:
    """Applique les normalisations sur un dict plat (source préservée)."""
    for spec in specs:
        source_val = attrs.get(spec.source_key)
        attrs[spec.target_key] = (
            None if source_val is None else spec.mapping.get(source_val, source_val)
        )
    return attrs


def _default_enrich(code: str, attrs: dict | None, subcategory: str | None) -> dict:
    return dict(attrs or {})


@dataclass(frozen=True)
class CategoryRule:
    """Configuration complète d'une famille produit."""

    code: str
    category_name: str = ""

    # --- Identité de matching ---
    identity_keys: tuple[str, ...] = ()
    product_key_map: Mapping[str, str] = field(default_factory=dict)
    normalizations: tuple[NormalizationSpec, ...] = ()

    # --- Sélection des produits PMC candidats ---
    pmc_code_prefixes: tuple[str, ...] = ()
    pmc_category_equals: tuple[str, ...] = ()
    pmc_subcategory_equals: tuple[str, ...] = ()

    # --- Pré-filtre SQL sur SupplierProduct.designation (motifs sans %) ---
    designation_ilike: tuple[str, ...] = ()

    # --- Stratégie de repli / tolérances ---
    allow_high: bool = False
    partial_strategy: str = PARTIAL_DIMS
    optional_compat_keys: tuple[str, ...] = ()
    dim_keys_for_partial: tuple[str, ...] = ()
    high_type_key: str | None = None
    hierarchy_keys: tuple[str, ...] = ()

    # --- Schéma catégorie (ProductCategory + ProductAttributeDef) ---
    attribute_defs: tuple[Mapping[str, Any], ...] = ()
    reference_unit_default: str | None = "pièce"
    schema_version: str = "1"

    # --- Versions tracées dans les propositions / features ---
    algorithm_version: str = ALGORITHM_VERSION_GENERIC_V2
    extractor_version: str = EXTRACTOR_VERSION_GENERIC_V2

    # --- Callables métier ---
    extract: Callable[..., Any] | None = None
    enrich_product_attrs: Callable[[str, dict | None, str | None], dict] = _default_enrich

    def product_key(self, extracted_key: str) -> str:
        return self.product_key_map.get(extracted_key, extracted_key)

    def identity_complete(self, extracted: Mapping[str, Any]) -> bool:
        """Toutes les clés d'identité renseignées (UNKNOWN interdit)."""
        return all(extracted.get(k) is not None for k in self.identity_keys)

    def normalize(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return apply_normalization_specs(attrs, self.normalizations)


_REGISTRY: dict[str, CategoryRule] = {}


def register_rule(rule: CategoryRule) -> CategoryRule:
    _REGISTRY[rule.code] = rule
    return rule


def get_rule(code: str) -> CategoryRule:
    try:
        return _REGISTRY[code]
    except KeyError:
        raise KeyError(f"Aucune CategoryRule enregistrée pour {code!r}") from None


def registered_codes() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
