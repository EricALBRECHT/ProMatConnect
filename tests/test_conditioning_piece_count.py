"""Conditionnement dérivé de la désignation (boîte N pièces) — garde-fous kits."""

from __future__ import annotations

from decimal import Decimal

from app.models import SupplierProduct
from app.services.conditioning import resolve_reference_quantity


def _sp(
    designation: str,
    *,
    reference_quantity: Decimal = Decimal("1"),
    packaging_quantity: Decimal = Decimal("1"),
) -> SupplierProduct:
    return SupplierProduct(
        supplier_id=1,
        supplier_reference="test-ref",
        designation=designation,
        supplier_unit="La pièce",
        reference_quantity=reference_quantity,
        packaging_quantity=packaging_quantity,
    )


def test_boite_500_vis_agglo():
    assert resolve_reference_quantity(
        _sp("Vis agglo turbo 3,5 x 25 mm 500 pièces")
    ) == Decimal("500")


def test_boite_100_vis_agglo():
    assert resolve_reference_quantity(
        _sp("Vis agglo plates 3,5 x 25 mm 100 pièces")
    ) == Decimal("100")


def test_produit_unitaire_reste_1():
    assert resolve_reference_quantity(
        _sp("Plaque de plâtre BA13 Purelight 2500 x 1200 mm")
    ) == Decimal("1")


def test_ensemble_kit_3_pieces_ne_devient_pas_3():
    assert resolve_reference_quantity(
        _sp("Ensemble de robinetterie salle de bain — 3 pièces")
    ) == Decimal("1")
    assert resolve_reference_quantity(
        _sp("Kit fixation étagère 3 pièces")
    ) == Decimal("1")
    assert resolve_reference_quantity(
        _sp("Meuble vasque 2 pièces blanc")
    ) == Decimal("1")


def test_resolve_keeps_explicit_reference_quantity():
    assert resolve_reference_quantity(
        _sp(
            "Vis agglo turbo 3,5 x 25 mm 500 pièces",
            reference_quantity=Decimal("200"),
        )
    ) == Decimal("200")


def test_cheville_100_pieces():
    assert resolve_reference_quantity(
        _sp("Chevilles nylon 6 x 30 mm - 100 pièces")
    ) == Decimal("100")


def test_lot_de_rails():
    assert resolve_reference_quantity(
        _sp("Lot de 10 rails métalliques R48")
    ) == Decimal("10")


def test_robinet_et_raccord_2_pieces_restent_1():
    """« 2 pièces » = type produit plomberie, pas un pack de 2 PMC."""
    assert resolve_reference_quantity(
        _sp("Robinet mitigeur thermostatique 2 pièces chrome")
    ) == Decimal("1")
    assert resolve_reference_quantity(
        _sp("Raccord à souder cuivre 2 pièces Ø 16")
    ) == Decimal("1")


def test_cheville_avec_vis_reste_1():
    assert resolve_reference_quantity(
        _sp("Cheville multi 8mm avec vis — 30pcs")
    ) == Decimal("1")
    assert resolve_reference_quantity(
        _sp("Vis et chevilles universelles 8x40 — 25pcs")
    ) == Decimal("1")
    assert resolve_reference_quantity(
        _sp("Chevilles + pattes à vis 6x40 — boîte de 50")
    ) == Decimal("1")


def test_coffret_outillage_reste_1():
    assert resolve_reference_quantity(
        _sp("Coffret d'accessoires de perçage — 49 pièces")
    ) == Decimal("1")


def test_parenthese_composition_reste_1():
    assert resolve_reference_quantity(
        _sp("Collier de fixation Ø 20 (2 pièces)")
    ) == Decimal("1")
