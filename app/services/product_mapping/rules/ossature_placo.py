"""CategoryRule OSSATURE_PLACO — IdentityModel LINEAR_PROFILE.

La longueur fournisseur (length_mm) n'est jamais modifiée. Le matching utilise
nominal_length_mm via la table explicite du modèle (2490→2500, 2990→3000).
"""

from __future__ import annotations

from typing import Any

from app.models.product_mapping import (
    ALGORITHM_VERSION_OSSATURE_V1,
    CATEGORY_OSSATURE_PLACO,
    EXTRACTOR_VERSION_OSSATURE_V1,
    KIND_FOURRURE,
    KIND_MONTANT,
    KIND_RAIL,
)
from app.services.product_mapping.identity_models import (
    LINEAR_PROFILE,
    NOMINAL_LENGTH_MAP,
    NOMINAL_LENGTH_SPEC,
)
from app.services.product_mapping.rules.base import CategoryRule, register_rule

# Compat imports historiques (tests / extracteur).
IDENTITY_KEYS = LINEAR_PROFILE.identity_keys

OSSATURE_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "kind",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": [KIND_RAIL, KIND_MONTANT, KIND_FOURRURE],
    },
    {
        "key": "profile",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": ["R48", "R70", "R90", "R100", "M48", "M70", "M90", "F45"],
    },
    {
        "key": "length_mm",
        "data_type": "int",
        "required": True,
        "unit": "mm",
        "match_role": "identity",
    },
]


def kind_from_product(
    code: str, attrs: dict | None = None, subcategory: str | None = None
) -> str | None:
    c = (code or "").upper()
    if c.startswith("PMC-RAIL-"):
        return KIND_RAIL
    if c.startswith("PMC-MONTANT-"):
        return KIND_MONTANT
    if c.startswith("PMC-FOURRURE-"):
        return KIND_FOURRURE
    sub = (subcategory or "").lower()
    if "rail" in sub:
        return KIND_RAIL
    if "montant" in sub:
        return KIND_MONTANT
    if "fourrure" in sub:
        return KIND_FOURRURE
    if attrs and attrs.get("kind"):
        return str(attrs["kind"]).upper()
    return None


def product_identity(
    code: str = "", attrs: dict | None = None, subcategory: str | None = None
) -> dict[str, Any]:
    source = dict(attrs or {})
    return {
        "kind": kind_from_product(code, source, subcategory),
        "profile": source.get("profile"),
        "length_mm": source.get("length_mm"),
    }


def _extract(designation: str, category_path: str | None = None):
    from app.services.product_mapping.ossature_extractor import extract_ossature_placo

    return extract_ossature_placo(designation=designation)


OSSATURE_PLACO_RULE = register_rule(
    CategoryRule(
        code=CATEGORY_OSSATURE_PLACO,
        category_name="Ossature placo",
        identity=LINEAR_PROFILE,
        pmc_code_prefixes=("PMC-RAIL-", "PMC-MONTANT-", "PMC-FOURRURE-"),
        pmc_category_equals=("Ossature",),
        designation_ilike=(
            "rail",
            "montant",
            "fourrure",
            "R48",
            "R70",
            "M48",
            "M70",
            "F45",
            "ossature",
        ),
        attribute_defs=tuple(OSSATURE_ATTR_DEFS),
        reference_unit_default="pièce",
        algorithm_version=ALGORITHM_VERSION_OSSATURE_V1,
        extractor_version=EXTRACTOR_VERSION_OSSATURE_V1,
        extract=_extract,
        enrich_product_attrs=product_identity,
    )
)

__all__ = [
    "IDENTITY_KEYS",
    "NOMINAL_LENGTH_MAP",
    "NOMINAL_LENGTH_SPEC",
    "OSSATURE_ATTR_DEFS",
    "OSSATURE_PLACO_RULE",
    "kind_from_product",
    "product_identity",
]
