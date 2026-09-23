from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.connectors.base import AgencyData, ConnectorOffer, SupplierConnector
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.services.comparison import ComparisonService, line_cost, required_packs
from app.services.distance import haversine_km


class StubConnector(SupplierConnector):
    def __init__(self, name, offers):
        self.name, self.offers = name, offers

    @property
    def supplier_name(self):
        return self.name

    def get_offers(self, product_ids):
        return [o for o in self.offers if o.product_id in product_ids]


def offer(
    supplier="A",
    product_id=1,
    price="10.00",
    stock=10,
    pack="1",
    agency_id=1,
    latitude=48.8566,
    minutes=30,
):
    return ConnectorOffer(
        supplier=supplier,
        product_id=product_id,
        price=Decimal(price),
        stock=stock,
        reference_quantity=Decimal(pack),
        supplier_reference=f"{supplier}-{product_id}",
        supplier_unit="lot",
        preparation_minutes=minutes,
        updated_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        agency=AgencyData(
            id=agency_id,
            name=f"Agence {agency_id}",
            address="Adresse fictive",
            postal_code="75001",
            city="Paris",
            latitude=latitude,
            longitude=2.3522,
        ),
    )


def compare(offers, quantities):
    products = {
        pid: ProductRead(
            id=pid,
            code=f"PMC{pid:04d}",
            name=f"Produit {pid}",
            category="Test",
            reference_unit="m",
            description=None,
        )
        for pid in quantities
    }
    lines = [CartLine(product_id=pid, quantity=Decimal(q)) for pid, q in quantities.items()]
    service = ComparisonService(
        [StubConnector(name, [o for o in offers if o.supplier == name]) for name in ["A", "B"]],
        48.8566,
        2.3522,
    )
    return service.compare(lines, products)


def test_exact_decimal_price():
    assert line_cost(offer(price="0.10"), Decimal("3")) == Decimal("0.30")


@pytest.mark.parametrize(
    "quantity,pack,expected",
    [("30", "1", 30), ("31", "25", 2), ("25", "25", 1), ("0.001", "6", 1), ("2.5", "0.5", 5)],
)
def test_unit_conversion(quantity, pack, expected):
    assert required_packs(Decimal(quantity), Decimal(pack)) == expected


def test_availability_boundary():
    result = compare([offer(stock=2, pack="25")], {1: "50"}).options[0]
    assert result.valid and result.total == Decimal("20")
    result = compare([offer(stock=2, pack="25")], {1: "50.001"}).options[0]
    assert not result.valid and result.total is None


def test_mon_supplier_can_use_multiple_agencies():
    result = compare(
        [offer(), offer(product_id=2, agency_id=2, minutes=120)], {1: "2", 2: "3"}
    ).options[0]
    assert result.valid
    assert result.total == Decimal("50")
    assert result.supplier_count == 1 and result.agency_count == 2
    assert result.max_preparation_minutes == 120


def test_multi_supplier_cheapest():
    result = compare(
        [
            offer(price="10"),
            offer(product_id=2, price="20"),
            offer("B", price="15", agency_id=3),
            offer("B", product_id=2, price="5", agency_id=3),
        ],
        {1: "2", 2: "3"},
    )
    assert [o.total for o in result.options] == [Decimal("80"), Decimal("45"), Decimal("35")]
    assert result.options[2].supplier_count == 2
    assert result.options[2].agency_count == 2


def test_choose_available_not_unavailable_cheapest():
    result = compare(
        [offer(price="1", stock=1), offer("B", price="12", agency_id=2)], {1: "2"}
    ).options[-1]
    assert result.valid
    assert result.available[0].supplier == "B"
    assert result.total == Decimal("24")


def test_compare_real_pack_cost_not_unit_price():
    result = compare(
        [offer(price="9", pack="10"), offer("B", price="2", agency_id=2)], {1: "1"}
    ).options[-1]
    assert result.total == Decimal("2")
    assert result.available[0].supplier == "B"


def test_surplus_disclosed():
    line = compare([offer(price="12.34", pack="25")], {1: "26"}).options[0].available[0]
    assert line.packs == 2
    assert line.purchased_quantity == Decimal("50")
    assert line.requested_quantity == Decimal("26")
    assert line.line_total == Decimal("24.68")


def test_partial_option_has_no_total():
    result = compare([offer()], {1: "1", 2: "3"}).options[0]
    assert not result.valid and result.total is None
    assert result.available_subtotal == Decimal("10")
    assert result.unavailable[0].product_id == 2


def test_all_unavailable():
    result = compare([offer(stock=0)], {1: "1"}).options[-1]
    assert result.total is None and result.available_subtotal == Decimal("0")
    assert result.supplier_count == result.agency_count == result.max_preparation_minutes == 0


def test_no_stock_pooling_between_agencies():
    result = compare([offer(stock=2), offer(stock=2, agency_id=2)], {1: "3"}).options[0]
    assert not result.valid


def test_nearest_agency_breaks_price_tie():
    result = compare([offer(agency_id=2, latitude=49), offer(agency_id=1)], {1: "1"}).options[0]
    assert result.available[0].agency_id == 1
    assert result.agencies[0].distance_km == 0


def test_agencies_deduplicated():
    result = compare([offer(), offer(product_id=2)], {1: "1", 2: "1"}).options[0]
    assert result.agency_count == 1


def test_distance_paris_lyon():
    assert haversine_km(48.8566, 2.3522, 45.764, 4.8357) == pytest.approx(391.5, abs=1)


def test_distance_identical():
    assert haversine_km(48, 2, 48, 2) == 0


def test_distance_antipodes():
    assert haversine_km(0, 0, 0, 180) == pytest.approx(20015.09, abs=0.1)
