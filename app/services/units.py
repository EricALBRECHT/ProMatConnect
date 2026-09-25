"""Normalisation et compatibilité des unités Product / SupplierProduct."""

from __future__ import annotations

# Unités de besoin Product acceptées (forme canonique affichée).
ALLOWED_PRODUCT_UNITS: tuple[str, ...] = (
    "pièce",
    "m",
    "m²",
    "kg",
    "L",
    "sac",
    "rouleau",
    "boîte",
    "seau",
    "panneau",
    "cartouche",
    "plaque",  # legacy démo uniquement — préférer pièce pour le besoin
)

_ALIASES: dict[str, str] = {
    "piece": "pièce",
    "pièce": "pièce",
    "pieces": "pièce",
    "pièces": "pièce",
    "pcs": "pièce",
    "u": "pièce",
    "unité": "pièce",
    "unite": "pièce",
    "m": "m",
    "ml": "m",
    "mètre": "m",
    "metre": "m",
    "mètres": "m",
    "metres": "m",
    "m2": "m²",
    "m²": "m²",
    "m^2": "m²",
    "mètres²": "m²",
    "metres2": "m²",
    "kg": "kg",
    "kilo": "kg",
    "l": "L",
    "L": "L",
    "litre": "L",
    "litres": "L",
    "sac": "sac",
    "rouleau": "rouleau",
    "boite": "boîte",
    "boîte": "boîte",
    "seau": "seau",
    "panneau": "panneau",
    "cartouche": "cartouche",
    "plaque": "plaque",
    "lot": "lot",  # supplier_unit seulement
}


def normalize_unit(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    key = text.lower().replace(" ", "")
    # Préserver symboles Unicode déjà normalisés.
    if text in ALLOWED_PRODUCT_UNITS:
        return text
    if text == "m²" or key in {"m2", "m²", "m^2"}:
        return "m²"
    if key in {"piece", "pièce", "pieces", "pièces", "pcs"}:
        return "pièce"
    return _ALIASES.get(key) or _ALIASES.get(text.lower()) or text


def units_compatible(product_unit: str | None, supplier_ref_unit: str | None) -> bool:
    """True si l'offre peut participer au comparateur (pas de conversion implicite)."""
    p = normalize_unit(product_unit)
    s = normalize_unit(supplier_ref_unit)
    if p is None:
        return False
    if s is None:
        # Pas d'unité SP : on assume Product.reference_unit.
        return True
    return p == s


def assert_allowed_product_unit(raw: str) -> str:
    canonical = normalize_unit(raw)
    if canonical is None or canonical not in ALLOWED_PRODUCT_UNITS:
        allowed = ", ".join(ALLOWED_PRODUCT_UNITS)
        raise ValueError(f"Unité non supportée : {raw!r}. Autorisées : {allowed}.")
    return canonical
