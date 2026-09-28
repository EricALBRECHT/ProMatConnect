"""Solutions incomplètes : une ligne indisponible ne bloque plus le reste."""

from datetime import datetime, timezone
from decimal import Decimal

from app.schemas.approvisionnement import ApprovisionnementWrite
from tests.test_comparison import compare, offer


def _five():
    return {i: "1" for i in range(1, 6)}


def test_complete_basket_stays_complete():
    offers = [offer(product_id=i, price="10") for i in range(1, 6)]
    result = compare(offers, _five()).options[0]
    assert result.valid
    assert result.lines_requested == 5
    assert result.lines_available == 5
    assert result.lines_partial == 0
    assert result.lines_unavailable == 0
    assert result.coverage_rate == Decimal("100.00")
    assert result.total == Decimal("50")


def test_zero_stock_line_keeps_the_other_lines():
    offers = [offer(product_id=i, price="10") for i in range(1, 5)]
    offers.append(offer(product_id=5, price="99", stock=0))
    result = compare(offers, _five()).options[0]
    assert not result.valid
    assert result.total is None
    assert result.available_subtotal == Decimal("40")
    assert result.lines_available == 4
    assert result.lines_requested == 5
    assert result.coverage_rate == Decimal("80.00")
    missing = result.unavailable[0]
    assert missing.product_id == 5
    assert missing.availability == "out_of_stock"
    assert missing.reason == "Indisponible dans ce dépôt"
    assert 5 not in {line.product_id for line in result.available}


def test_missing_offer_keeps_the_other_lines():
    offers = [offer(product_id=i, price="10") for i in range(1, 5)]
    result = compare(offers, _five()).options[0]
    assert not result.valid
    assert result.total is None
    assert result.lines_available == 4
    assert result.lines_requested == 5
    assert result.unavailable[0].availability == "unavailable"
    assert result.unavailable[0].product_id == 5
    assert result.available_subtotal == Decimal("40")


def test_partial_stock_is_not_zero():
    result = compare([offer(price="2", stock=6)], {1: "10"}).options[0]
    line = result.available[0]
    assert line.availability == "partial"
    assert line.purchased_quantity == Decimal("6")
    assert line.missing_quantity == Decimal("4")
    assert line.line_total == Decimal("12")
    assert result.lines_partial == 1
    assert result.lines_available == 0
    assert not result.valid
    assert result.total is None
    assert result.available_subtotal == Decimal("12")


def test_nothing_available_is_not_a_free_complete_solution():
    offers = [offer(product_id=i, stock=0, price="10") for i in range(1, 6)]
    data = compare(offers, _five())
    result = data.options[0]
    assert not result.valid
    assert result.total is None
    assert result.lines_available == 0
    assert result.lines_requested == 5
    assert result.available_subtotal == Decimal("0")
    assert result.coverage_rate == Decimal("0.00")
    assert all(
        not strategy.valid and strategy.material_total is None
        for strategy in data.strategies
    )


def test_confirm_incomplete_keeps_the_unsatisfied_need():
    offers = [offer(product_id=i, price="10") for i in range(1, 5)]
    offers.append(offer(product_id=5, stock=0, price="50"))
    strategy = compare(offers, _five()).strategies[0]
    assert strategy.lines_requested == 5
    assert len(strategy.lines) == 4
    assert len(strategy.unavailable) == 1
    assert strategy.unavailable[0].product_id == 5
    assert strategy.unavailable[0].availability == "out_of_stock"
    assert 5 not in {line.product_id for line in strategy.lines}
    assert all(line.line_total > 0 for line in strategy.lines)
    assert strategy.material_total is None
    saved = ApprovisionnementWrite(
        updated_at=datetime.now(timezone.utc),
        needs_fingerprint="1:1.000|2:1.000|3:1.000|4:1.000|5:1.000",
        strategy=strategy,
    )
    kept = {line.product_id for line in saved.strategy.lines}
    kept.update(line.product_id for line in saved.strategy.unavailable)
    assert kept == {1, 2, 3, 4, 5}
    assert saved.strategy.unavailable[0].product_id not in {
        line.product_id for line in saved.strategy.lines
    }
