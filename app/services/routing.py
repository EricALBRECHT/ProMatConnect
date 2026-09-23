"""Routage simulé d'une tournée fermée et optimisation de l'ordre injectables."""

from abc import ABC, abstractmethod
from itertools import permutations
from math import cos, radians

from app.schemas.location import RouteLeg, RoutePoint, RouteResult


class RoutingService(ABC):
    provider = "unknown"
    simulated = False

    @abstractmethod
    def leg(self, start: RoutePoint, end: RoutePoint) -> RouteLeg: ...

    def route(self, origin: RoutePoint, stops: list[RoutePoint]) -> RouteResult:
        """Calcule les segments pour l'ordre fourni, retour à l'origine inclus."""
        points = [origin, *stops, origin] if stops else [origin]
        legs = [self.leg(a, b) for a, b in zip(points, points[1:])]
        return RouteResult(
            points=points,
            legs=legs,
            total_distance_km=round(sum(leg.distance_km for leg in legs), 2),
            travel_minutes=round(sum(leg.duration_minutes for leg in legs), 2),
            provider=self.provider,
            simulated=self.simulated,
            order_method="given_order",
        )


class FakeRoutingService(RoutingService):
    """Réseau synthétique en grille : ni Haversine, ni données routières réelles.

    Distances : projection locale Manhattan × 1.15, arrondie au centième de km.
    Temps : 3 minutes/km + 2 minutes par segment non nul (carrefours simulés).
    Une matrice dirigée (clé début, clé fin) -> (km, minutes) peut remplacer la grille.
    """

    provider = "fake_grid_v1"
    simulated = True

    def __init__(self, matrix: dict[tuple[str, str], tuple[float, float]] | None = None):
        self.matrix = matrix

    def leg(self, start: RoutePoint, end: RoutePoint) -> RouteLeg:
        if self.matrix is not None:
            # Échec explicite si la matrice de test est incomplète, jamais de trajet inventé.
            distance, duration = self.matrix[(start.key, end.key)]
        else:
            north = abs(end.latitude - start.latitude) * 111.32
            east = (
                abs(end.longitude - start.longitude)
                * 111.32
                * cos(radians((start.latitude + end.latitude) / 2))
            )
            distance = round((north + east) * 1.15, 2)
            duration = round(distance * 3 + (2 if distance else 0), 2)
        return RouteLeg(start=start, end=end, distance_km=distance, duration_minutes=duration)


class RouteOrderOptimizer(ABC):
    @abstractmethod
    def optimize(
        self, origin: RoutePoint, stops: list[RoutePoint], routing: RoutingService
    ) -> RouteResult: ...


class ExactRouteOrderOptimizer(RouteOrderOptimizer):
    """Toutes les permutations jusqu'à 3 arrêts ; Held-Karp au-delà.

    Priorité durée, puis distance, puis ordre des identifiants. Matrice locale à
    chaque tournée ; le fournisseur injecté est caché pendant une comparaison.
    """

    def optimize(
        self, origin: RoutePoint, stops: list[RoutePoint], routing: RoutingService
    ) -> RouteResult:
        stops = sorted(stops, key=lambda p: p.key)
        if len(stops) <= 3:
            result = min(
                (routing.route(origin, list(order)) for order in permutations(stops)),
                key=lambda r: (
                    r.travel_minutes,
                    r.total_distance_km,
                    tuple(p.key for p in r.points),
                ),
            )
            return result.model_copy(update={"order_method": "exhaustive_permutations"})
        # Programmation dynamique : état (masque des arrêts visités, dernier arrêt).
        n = len(stops)
        points = [origin, *stops]
        legs = {
            (i, j): routing.leg(a, b)
            for i, a in enumerate(points)
            for j, b in enumerate(points)
            if i != j
        }

        def weights(i, j):
            leg = legs[i, j]
            return round(leg.duration_minutes * 100), round(leg.distance_km * 100)

        states = {}
        for j in range(n):
            time, distance = weights(0, j + 1)
            states[1 << j, j] = (time, distance, (j,))
        for mask in range(1, 1 << n):
            for j in range(n):
                state = states.get((mask, j))
                if state is None:
                    continue
                for k in range(n):
                    if mask & (1 << k):
                        continue
                    time, distance = weights(j + 1, k + 1)
                    candidate = (state[0] + time, state[1] + distance, (*state[2], k))
                    key = (mask | (1 << k), k)
                    if key not in states or candidate < states[key]:
                        states[key] = candidate
        final = []
        for j in range(n):
            time, distance, order = states[(1 << n) - 1, j]
            back_time, back_distance = weights(j + 1, 0)
            final.append((time + back_time, distance + back_distance, order))
        order = min(final)[2]
        result = routing.route(origin, [stops[j] for j in order])
        return result.model_copy(update={"order_method": "held_karp_exact"})


class CachedRoutingService(RoutingService):
    """Cache limité à la requête : pas de conservation des positions utilisateur."""

    def __init__(self, delegate: RoutingService):
        self.delegate = delegate
        self.provider = delegate.provider
        self.simulated = delegate.simulated
        self.cache: dict[tuple[RoutePoint, RoutePoint], RouteLeg] = {}

    def leg(self, start: RoutePoint, end: RoutePoint) -> RouteLeg:
        key = (start, end)
        if key not in self.cache:
            self.cache[key] = self.delegate.leg(start, end)
        return self.cache[key]
