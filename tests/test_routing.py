from itertools import permutations

import pytest

from app.schemas.location import RoutePoint
from app.services.routing import CachedRoutingService, ExactRouteOrderOptimizer, FakeRoutingService


def point(key, latitude=48.8566, longitude=2.3522):
    return RoutePoint(key=key, label=key, latitude=latitude, longitude=longitude)


def test_fake_route_grid_and_no_haversine():
    service = FakeRoutingService()
    origin = point("origin")
    stop = point("a", 48.87, 2.4)
    result = service.route(origin, [stop])
    assert result == service.route(origin, [stop])
    assert result.simulated and result.provider == "fake_grid_v1"
    assert len(result.legs) == 2
    assert result.points[0] == result.points[-1] == origin
    assert result.total_distance_km == round(sum(leg.distance_km for leg in result.legs), 2)
    assert result.travel_minutes == round(sum(leg.duration_minutes for leg in result.legs), 2)


def test_directed_round_trip_one_stop():
    result = FakeRoutingService({("origin", "a"): (4.2, 8), ("a", "origin"): (5, 11)}).route(
        point("origin"),
        [point("a")],
    )
    assert result.total_distance_km == 9.2 and result.travel_minutes == 19


@pytest.mark.parametrize("count", [2, 3, 4])
def test_best_visit_order_against_all_permutations(count):
    origin = point("origin")
    stops = [point(str(i)) for i in range(count)]
    keys = ["origin", *(s.key for s in stops)]
    matrix = {(a, b): (10, 25) for a in keys for b in keys if a != b}
    target = ["origin", *(s.key for s in reversed(stops)), "origin"]
    for a, b in zip(target, target[1:]):
        matrix[a, b] = (2, 3)
    routing = FakeRoutingService(matrix)
    result = ExactRouteOrderOptimizer().optimize(origin, stops, routing)
    all_routes = [routing.route(origin, list(order)) for order in permutations(stops)]
    expected = min(all_routes, key=lambda route: (route.travel_minutes, route.total_distance_km))
    assert result.travel_minutes == expected.travel_minutes
    assert [p.key for p in result.points] == target
    assert result.order_method == ("exhaustive_permutations" if count <= 3 else "held_karp_exact")


def test_multistop_route_is_not_sum_of_origin_distances():
    routing = FakeRoutingService(
        {("origin", "a"): (4, 8), ("a", "b"): (6, 11), ("b", "origin"): (5, 12)}
    )
    result = routing.route(point("origin"), [point("a"), point("b")])
    assert result.total_distance_km == 15 and result.travel_minutes == 31
    assert len(result.legs) == 3


def test_route_without_stops():
    result = ExactRouteOrderOptimizer().optimize(point("origin"), [], FakeRoutingService())
    assert len(result.points) == 1 and result.legs == []
    assert result.travel_minutes == result.total_distance_km == 0


def test_zero_distance():
    result = FakeRoutingService().route(point("origin"), [point("a")])
    assert result.travel_minutes == result.total_distance_km == 0


def test_route_cache_scoped_to_instance():
    class CountingRouter(FakeRoutingService):
        calls = 0

        def leg(self, start, end):
            self.calls += 1
            return super().leg(start, end)

    delegate = CountingRouter()
    cache = CachedRoutingService(delegate)
    cache.route(point("o"), [point("a", latitude=49)])
    cache.route(point("o"), [point("a", latitude=49)])
    assert delegate.calls == 2
    CachedRoutingService(delegate).route(point("o"), [point("a", latitude=49)])
    assert delegate.calls == 4
