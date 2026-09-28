"""Gedimat : délai et commande restent affichables, sans bloquer le panier."""

from decimal import Decimal

from app.connectors.gedimat.availability import normalize_availability
from tests.test_comparison import compare, offer


def test_disponible_with_zero_stock_stays_available():
    assert (
        normalize_availability(
            {
                "stock": 0,
                "dispo": 0,
                "quantite_stock": 2,
                "is_available": True,
                "availability": "Disponible",
                "vendable": True,
            }
        )
        == "available"
    )


def test_delay_and_order_are_not_zero_stock():
    assert normalize_availability({"availability": "Sous 10 jours", "vendable": True, "stock": 10}) == "delayed"
    assert normalize_availability({"availability": "Sur commande", "vendable": True, "stock": 999}) == "order_only"
    assert normalize_availability({"availability": "Disponible", "vendable": False}) == "unavailable"


def test_mixed_gedimat_lines_are_not_a_complete_immediate_basket():
    offers = [
        offer(product_id=1, price="10", stock=5),
        offer(product_id=2, price="8", stock=5).model_copy(update={"fulfillment": "delayed"}),
        offer(product_id=3, price="6", stock=5).model_copy(update={"fulfillment": "order_only"}),
    ]
    result = compare(offers, {1: "1", 2: "1", 3: "1"}).options[0]
    kinds = {line.product_id: line.availability for line in result.available}
    assert kinds == {1: "available", 2: "delayed", 3: "order_only"}
    assert result.lines_available == 1
    assert result.lines_requested == 3
    assert not result.valid
    assert result.total is None
    assert result.available_subtotal == Decimal("24")
    assert result.unavailable == []
