"""Exploration exacte des sous-ensembles d'agences et seuils de préparation du MVP."""

from collections.abc import Callable
from decimal import Decimal
from itertools import combinations

from app.connectors.base import ConnectorOffer
from app.schemas.comparison import (
    CartLine,
    ComparisonOption,
    ProcurementStrategy,
    StrategyDelta,
    UnavailableLine,
)
from app.schemas.location import ResolvedOrigin, RoutePoint, RouteResult
from app.services.procurement_cost import ProcurementCostService
from app.services.routing import CachedRoutingService, RouteOrderOptimizer, RoutingService

OptionFactory = Callable[[list[ConnectorOffer]], ComparisonOption]


class OptimizationLimitError(ValueError):
    pass


class ProcurementOptimizer:
    def __init__(
        self,
        routing: RoutingService,
        order_optimizer: RouteOrderOptimizer,
        costs: ProcurementCostService,
        max_agencies: int = 8,
    ):
        self.routing = CachedRoutingService(routing)
        self.order_optimizer = order_optimizer
        self.costs = costs
        self.max_agencies = max_agencies

    def optimize(
        self,
        lines: list[CartLine],
        offers: list[ConnectorOffer],
        origin: ResolvedOrigin,
        option_factory: OptionFactory,
    ) -> tuple[list[ProcurementStrategy], StrategyDelta | None]:
        quantities = {line.product_id: line.quantity for line in lines}
        eligible = [
            offer
            for offer in offers
            if offer.product_id in quantities
            and offer.available_quantity >= quantities[offer.product_id]
        ]
        agencies = {offer.agency.id: offer.agency for offer in eligible}
        global_option = option_factory(eligible)
        labels = [
            ("single_stop", "1 seul arrêt"),
            ("minimum_materials", "Prix matériaux minimum"),
            ("best_compromise", "Meilleur compromis"),
        ]
        if not global_option.valid:
            return [
                ProcurementStrategy(
                    key=key,
                    title=title,
                    valid=False,
                    explanation="Panier incomplet : quantités indisponibles.",
                    unavailable=global_option.unavailable,
                )
                for key, title in labels
            ], None
        if len(agencies) > self.max_agencies:
            raise OptimizationLimitError(
                f"L'optimiseur MVP est limité à {self.max_agencies} agences éligibles. "
                "Réduisez le périmètre avant de comparer."
            )
        origin_point = RoutePoint(
            key="origin",
            label=origin.label,
            latitude=origin.latitude,
            longitude=origin.longitude,
        )
        route_cache: dict[tuple[int, ...], RouteResult] = {}
        seen_allocations = set()
        candidates = []
        thresholds = sorted({offer.preparation_minutes for offer in eligible})
        agency_ids = sorted(agencies)
        for count in range(1, len(agency_ids) + 1):
            for subset in combinations(agency_ids, count):
                subset_offers = [o for o in eligible if o.agency.id in subset]
                if {o.product_id for o in subset_offers} != set(quantities):
                    continue
                for threshold in thresholds:
                    subset_at_time = [
                        o for o in subset_offers if o.preparation_minutes <= threshold
                    ]
                    if {o.product_id for o in subset_at_time} != set(quantities):
                        continue
                    option = option_factory(subset_at_time)
                    # Chaque seuil représente le délai max autorisé pour la solution.
                    signature = tuple(
                        (line.product_id, line.agency_id, line.supplier_reference)
                        for line in option.available
                    )
                    if signature in seen_allocations:
                        continue
                    seen_allocations.add(signature)
                    used = tuple(sorted(agency.id for agency in option.agencies))
                    if used not in route_cache:
                        stops = [
                            RoutePoint(
                                key=f"agency:{i}",
                                agency_id=i,
                                label=agencies[i].name,
                                latitude=agencies[i].latitude,
                                longitude=agencies[i].longitude,
                            )
                            for i in used
                        ]
                        route_cache[used] = self.order_optimizer.optimize(
                            origin_point, stops, self.routing
                        )
                    route = route_cache[used]
                    distance = Decimal(str(route.total_distance_km))
                    travel = Decimal(str(route.travel_minutes))
                    breakdown = self.costs.calculate(
                        option.total,
                        distance,
                        travel,
                        option.max_preparation_minutes,
                        len(used),
                    )
                    stop_details = {a.id: a for a in option.agencies}
                    candidates.append(
                        ProcurementStrategy(
                            key="candidate",
                            title="",
                            valid=True,
                            explanation="",
                            material_total=option.total,
                            estimated_procurement_cost=breakdown.estimated_procurement_cost,
                            stops=[stop_details[p.agency_id] for p in route.points[1:-1]],
                            supplier_count=option.supplier_count,
                            total_distance_km=distance,
                            travel_minutes=travel,
                            max_preparation_minutes=option.max_preparation_minutes,
                            total_minutes=breakdown.total_minutes,
                            route=route,
                            lines=option.available,
                            cost_breakdown=breakdown,
                        )
                    )

        def cost_rank(candidate: ProcurementStrategy):
            return (
                candidate.estimated_procurement_cost,
                candidate.material_total,
                len(candidate.stops),
                candidate.travel_minutes,
                tuple(stop.id for stop in candidate.stops),
            )

        singles = [c for c in candidates if len(c.stops) == 1]
        single = (
            min(singles, key=cost_rank)
            if singles
            else ProcurementStrategy(
                key="single_stop",
                title="1 seul arrêt",
                valid=False,
                explanation="Aucune agence ne peut fournir le panier complet en un seul arrêt.",
                unavailable=[
                    UnavailableLine(
                        product_id=line.product_id,
                        product_name=line.product_name,
                        quantity=line.requested_quantity,
                        reason="Disponible séparément ; aucune agence ne couvre tout le panier.",
                    )
                    for line in global_option.available
                ],
            )
        )
        minimum = min(candidates, key=lambda c: (c.material_total, *cost_rank(c)))
        compromise = min(candidates, key=cost_rank)
        explanations = [
            "Coût d'approvisionnement le plus faible parmi les agences couvrant seules le panier.",
            "Prix total des matériaux le plus faible, conditionnements compris.",
            "Coût estimé minimal : matériaux, trajet, préparation et arrêts supplémentaires.",
        ]
        strategies = [
            candidate.model_copy(
                update={
                    "key": key,
                    "title": title,
                    "explanation": explanation if candidate.valid else candidate.explanation,
                }
            )
            for candidate, (key, title), explanation in zip(
                [single, minimum, compromise], labels, explanations
            )
        ]
        delta = (
            StrategyDelta(
                material_savings=single.material_total - minimum.material_total,
                extra_distance_km=minimum.total_distance_km - single.total_distance_km,
                extra_travel_minutes=minimum.travel_minutes - single.travel_minutes,
                extra_total_minutes=minimum.total_minutes - single.total_minutes,
            )
            if single.valid
            else None
        )
        return strategies, delta
