from decimal import Decimal
from itertools import product

import pytest
from pydantic import ValidationError
from test_comparison import StubConnector, compare, offer

from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.schemas.location import RoutePoint
from app.services.comparison import ComparisonService, line_cost
from app.services.optimization import OptimizationLimitError
from app.services.procurement_cost import CostParameters, ProcurementCostService
from app.services.routing import ExactRouteOrderOptimizer, FakeRoutingService

D = Decimal


def test_kilometre_cost():
    result = ProcurementCostService(
        CostParameters(time_value_per_hour=D(0), extra_stop_cost=D(0))
    ).calculate(
        D("100"),
        D("9.2"),
        D("16"),
        0,
        1,
    )
    assert result.distance_cost == D("4.60") and result.estimated_procurement_cost == D("104.60")


def test_time_value_includes_parallel_preparation_before_departure():
    result = ProcurementCostService(
        CostParameters(cost_per_km=D(0), extra_stop_cost=D(0))
    ).calculate(
        D("100"),
        D("9.2"),
        D("16"),
        30,
        1,
    )
    assert result.total_minutes == D("46") and result.time_cost == D("23.00")
    assert result.estimated_procurement_cost == D("123.00")


@pytest.mark.parametrize("stops,expected", [(0, "0"), (1, "0"), (2, "5"), (3, "10")])
def test_extra_stop_penalty(stops, expected):
    result = ProcurementCostService(CostParameters()).calculate(D(0), D(0), D(0), 0, stops)
    assert result.extra_stops_cost == D(expected)


def test_cost_components_round_half_up_individually():
    result = ProcurementCostService(CostParameters()).calculate(
        D("1.10"), D("0.01"), D("0.01"), 0, 1
    )
    assert result.distance_cost == result.time_cost == D("0.01")
    assert result.estimated_procurement_cost == D("1.12")


@pytest.mark.parametrize("field", ["cost_per_km", "time_value_per_hour", "extra_stop_cost"])
def test_negative_cost_parameter_rejected(field):
    with pytest.raises(ValidationError):
        CostParameters(**{field: D("-1")})


def test_one_stop_really_means_one_agency_not_one_supplier():
    data = compare(
        [offer(product_id=1, agency_id=1), offer(product_id=2, agency_id=2)], {1: "1", 2: "1"}
    )
    single, minimum, compromise = data.strategies
    assert not single.valid and single.material_total is None and single.route is None
    assert minimum.valid and len(minimum.stops) == 2 and minimum.supplier_count == 1
    assert data.minimum_vs_single is None and compromise.valid


def test_compromise_prefers_nearer_more_expensive_materials():
    data = compare(
        [
            offer(price="110", minutes=0),
            offer("B", price="100", agency_id=2, latitude=49.8, minutes=0),
        ],
        {1: "1"},
    )
    single, minimum, compromise = data.strategies
    assert minimum.material_total == D("100")
    assert compromise.material_total == single.material_total == D("110")
    assert compromise.estimated_procurement_cost < minimum.estimated_procurement_cost
    assert data.minimum_vs_single.material_savings == D("10")
    assert data.minimum_vs_single.extra_travel_minutes > 0


def test_compromise_can_choose_multiple_suppliers():
    data = compare(
        [
            offer(price="10", product_id=1, minutes=0),
            offer(price="100", product_id=2, minutes=0),
            offer("B", price="100", product_id=1, agency_id=2, minutes=0),
            offer("B", price="10", product_id=2, agency_id=2, minutes=0),
        ],
        {1: "1", 2: "1"},
    )
    compromise = data.strategies[2]
    assert compromise.material_total == D("20") and compromise.supplier_count == 2
    assert len(compromise.stops) == 2 and compromise.estimated_procurement_cost == D("25")


def test_preparation_threshold_can_select_costlier_reference_in_same_agency():
    slow = offer(price="10", minutes=120)
    fast = offer(price="20", minutes=0).model_copy(update={"supplier_reference": "FAST"})
    single, minimum, compromise = compare([slow, fast], {1: "1"}).strategies
    assert minimum.material_total == D("10")
    assert compromise.material_total == single.material_total == D("20")
    assert compromise.lines[0].supplier_reference == "FAST"


def test_unavailable_is_never_scored_as_a_complete_basket():
    data = compare([offer(stock=0)], {1: "1"})
    assert all(
        not strategy.valid
        and strategy.estimated_procurement_cost is None
        and strategy.material_total is None
        and strategy.route is None
        for strategy in data.strategies
    )
    assert all(s.unavailable[0].product_id == 1 for s in data.strategies)


def test_route_and_allocation_stop_order_agree():
    data = compare([offer(), offer(product_id=2, agency_id=2, latitude=48.9)], {1: "1", 2: "1"})
    result = data.strategies[1]
    assert [s.id for s in result.stops] == [p.agency_id for p in result.route.points[1:-1]]
    assert {line.agency_id for line in result.lines} == {stop.id for stop in result.stops}
    assert result.travel_minutes == D(str(result.route.travel_minutes))


def test_exact_compromise_against_all_allocations():
    # Oracle indépendant : toutes les affectations des 3 lignes, sans présélection.
    quantities = {1: "2", 2: "1", 3: "3"}
    offers = [
        offer(
            supplier,
            product_id=p,
            price=str(8 + ((p * a) % 7)),
            agency_id=a,
            latitude=48.85 + a * 0.005,
            minutes=((p + a) % 3) * 15,
        )
        for a, supplier in [(1, "A"), (2, "B"), (3, "B")]
        for p in quantities
    ]
    data = compare(offers, quantities)
    origin = RoutePoint(key="origin", label="Chantier", latitude=48.8566, longitude=2.3522)
    scores = []
    for assignment in product(*[[o for o in offers if o.product_id == p] for p in quantities]):
        by_agency = {o.agency.id: o.agency for o in assignment}
        stops = [
            RoutePoint(
                key=f"agency:{a.id}", label=a.name, latitude=a.latitude, longitude=a.longitude
            )
            for a in by_agency.values()
        ]
        route = ExactRouteOrderOptimizer().optimize(origin, stops, FakeRoutingService())
        materials = sum((line_cost(o, D(quantities[o.product_id])) for o in assignment), D(0))
        score = ProcurementCostService(CostParameters()).calculate(
            materials,
            D(str(route.total_distance_km)),
            D(str(route.travel_minutes)),
            max(o.preparation_minutes for o in assignment),
            len(stops),
        )
        scores.append(score.estimated_procurement_cost)
    assert data.strategies[2].estimated_procurement_cost == min(scores)


def test_configurable_zero_weights_and_agency_limit():
    offers = [offer(price="10", agency_id=1), offer("B", price="9", agency_id=2, latitude=49)]
    connectors = [StubConnector("A", offers[:1]), StubConnector("B", offers[1:])]
    products = {
        1: ProductRead(
            id=1, code="PMC0001", name="Test", category="Test", reference_unit="m", description=None
        )
    }
    lines = [CartLine(product_id=1, quantity=D(1))]
    service = ComparisonService(
        connectors,
        48.8566,
        2.3522,
        cost_parameters=CostParameters(
            cost_per_km=D(0),
            time_value_per_hour=D(0),
            extra_stop_cost=D(0),
        ),
    )
    result = service.compare(lines, products).strategies[2]
    assert result.material_total == result.estimated_procurement_cost == D("9")
    with pytest.raises(OptimizationLimitError):
        ComparisonService(connectors, 48.8566, 2.3522, max_agencies=1).compare(lines, products)
