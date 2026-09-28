"""Disponibilité Gedimat — distincte de la règle stock Brico.

Les valeurs brutes restent dans le cache (extras). La classe normalisée
ne transforme pas « Sous 10 jours » ni « Sur commande » en stock zéro.
"""

from __future__ import annotations

from typing import Any

AVAILABLE = "available"
DELAYED = "delayed"
ORDER_ONLY = "order_only"
UNAVAILABLE = "unavailable"

_LABELS = {
    AVAILABLE: "Disponible",
    DELAYED: "Sous 10 jours",
    ORDER_ONLY: "Sur commande",
    UNAVAILABLE: "Indisponible",
}


def normalize_availability(hit: dict[str, Any]) -> str:
    """AVAILABLE, DELAYED, ORDER_ONLY ou UNAVAILABLE."""
    vendable = hit.get("vendable")
    is_available = hit.get("is_available")
    if vendable is False or is_available is False:
        return UNAVAILABLE
    label = str(hit.get("availability") or "").strip().casefold()
    if label == "disponible":
        return AVAILABLE
    if "sous" in label and "jour" in label:
        return DELAYED
    if "commande" in label:
        return ORDER_ONLY
    if not label:
        return UNAVAILABLE
    return ORDER_ONLY


def availability_label(kind: str) -> str:
    return _LABELS.get(kind, kind)


def purchasable_quantity(hit: dict[str, Any]) -> int:
    """Quantité affichable, sans appliquer la règle Brico stock > 0.

    Pour « Disponible », quantite_stock peut être positif alors que stock vaut 0.
    """
    for key in ("quantite_stock", "dispo", "stock"):
        value = hit.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0
