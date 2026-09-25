"""Conversion HT ↔ TTC — Decimal uniquement, jamais de float monétaire.

Règle d'arrondi monétaire ProMatConnect (historique) :
    CENT = Decimal("0.01")
    ROUND_HALF_UP

Les montants internes (conversion) restent en précision pleine ;
l'arrondi monétaire s'applique à l'affichage et aux totaux de ligne.
Le prix source fournisseur n'est jamais modifié.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# Préférence unique de base de comparaison (B2B).
DEFAULT_COMPARE_TAX_BASIS = "HT"

CENT = Decimal("0.01")
MONEY_ROUNDING = ROUND_HALF_UP

# Plage raisonnable pour vat_rate CSV / saisie (pourcentage).
VAT_RATE_MIN = Decimal("0")
VAT_RATE_MAX = Decimal("100")

CONVERSION_UNAVAILABLE_MSG = (
    "Conversion HT/TTC indisponible : taux de TVA inconnu."
)


def money_round(value: Decimal) -> Decimal:
    """Arrondi monétaire projet : 2 décimales, ROUND_HALF_UP."""
    return Decimal(value).quantize(CENT, rounding=MONEY_ROUNDING)


def parse_vat_rate(raw: str | None) -> Decimal | None:
    """Parse une saisie vat_rate (20, 20.0, 20.00). None si vide."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", ".")
    if not text:
        return None
    return Decimal(text)


def validate_vat_rate(rate: Decimal) -> bool:
    return VAT_RATE_MIN <= rate <= VAT_RATE_MAX


def vat_factor(vat_rate: Decimal) -> Decimal:
    """1 + vat_rate/100 (ex. 20 → 1.20)."""
    return Decimal("1") + (Decimal(vat_rate) / Decimal("100"))


def convert_amount(
    amount: Decimal,
    source_basis: str,
    vat_rate: Decimal | None,
    target_basis: str,
) -> Decimal | None:
    """Convertit un montant source vers target_basis.

    Retourne None si la conversion est impossible (bases différentes + TVA inconnue).
    Ne quantize pas : précision pleine pour enchaîner packs × prix.
    """
    src = (source_basis or "HT").upper()
    tgt = (target_basis or "HT").upper()
    if src == tgt:
        return Decimal(amount)
    if vat_rate is None:
        return None
    rate = Decimal(vat_rate)
    if src == "TTC" and tgt == "HT":
        return Decimal(amount) / vat_factor(rate)
    if src == "HT" and tgt == "TTC":
        return Decimal(amount) * vat_factor(rate)
    return None


def can_compare_in_basis(source_basis: str, vat_rate: Decimal | None, target_basis: str) -> bool:
    src = (source_basis or "HT").upper()
    tgt = (target_basis or "HT").upper()
    if src == tgt:
        return True
    return vat_rate is not None


def resolve_comparison_basis(requested: str | None) -> str:
    """Une seule source de vérité pour la base de comparaison."""
    if requested in ("HT", "TTC"):
        return requested
    return DEFAULT_COMPARE_TAX_BASIS


def display_converted(
    amount: Decimal,
    source_basis: str,
    vat_rate: Decimal | None,
    target_basis: str,
) -> Decimal | None:
    """Montant converti puis arrondi pour affichage."""
    converted = convert_amount(amount, source_basis, vat_rate, target_basis)
    if converted is None:
        return None
    return money_round(converted)
