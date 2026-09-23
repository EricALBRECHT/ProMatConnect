from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.connectors.base import ConnectorOffer, SupplierConnector
from app.schemas.catalog import ProductRead
from app.schemas.comparison import (
    AgencyResult,
    CartLine,
    ComparisonOption,
    ComparisonResponse,
    SelectedLine,
    UnavailableLine,
)
from app.schemas.location import ResolvedOrigin
from app.services.distance import haversine_km
from app.services.optimization import ProcurementOptimizer
from app.services.procurement_cost import CostParameters, ProcurementCostService
from app.services.routing import (
    ExactRouteOrderOptimizer,
    FakeRoutingService,
    RouteOrderOptimizer,
    RoutingService,
)

CENT = Decimal("0.01")


def required_packs(quantity: Decimal, reference_quantity: Decimal) -> int:
    return int((quantity / reference_quantity).to_integral_value(rounding=ROUND_CEILING))


def line_cost(offer: ConnectorOffer, quantity: Decimal) -> Decimal:
    return (offer.price * required_packs(quantity, offer.reference_quantity)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


class ComparisonService:
    """Service sans ORM ni connaissance des fournisseurs : seules les interfaces sont injectées."""

    def __init__(
        self,
        connectors: list[SupplierConnector],
        latitude: float,
        longitude: float,
        *,
        origin: ResolvedOrigin | None = None,
        routing: RoutingService | None = None,
        cost_parameters: CostParameters | None = None,
        order_optimizer: RouteOrderOptimizer | None = None,
        max_agencies: int = 8,
    ):
        self.connectors = connectors
        self.latitude = latitude
        self.longitude = longitude
        self.origin = origin or ResolvedOrigin(
            latitude=latitude,
            longitude=longitude,
            type="site",
            label="Chantier",
            source="configuration",
        )
        self.latitude = self.origin.latitude
        self.longitude = self.origin.longitude
        self.cost_parameters = cost_parameters or CostParameters()
        self.routing = routing or FakeRoutingService()
        self.order_optimizer = order_optimizer or ExactRouteOrderOptimizer()
        self.max_agencies = max_agencies

    def compare(
        self, lines: list[CartLine], products: dict[int, ProductRead]
    ) -> ComparisonResponse:
        ids = [line.product_id for line in lines]
        offers = [offer for connector in self.connectors for offer in connector.get_offers(ids)]
        options = [
            self._option(
                f"supplier_{index}",
                f"Tout chez {connector.supplier_name}",
                lines,
                products,
                [offer for offer in offers if offer.supplier == connector.supplier_name],
            )
            for index, connector in enumerate(self.connectors, start=1)
        ]
        options.append(self._option("optimized", "Panier optimisé", lines, products, offers))
        strategies, delta = ProcurementOptimizer(
            self.routing,
            self.order_optimizer,
            ProcurementCostService(self.cost_parameters),
            self.max_agencies,
        ).optimize(
            lines,
            offers,
            self.origin,
            lambda selected: self._option("candidate", "", lines, products, selected),
        )
        return ComparisonResponse(
            origin=self.origin,
            cost_parameters=self.cost_parameters,
            strategies=strategies,
            minimum_vs_single=delta,
            user_latitude=self.latitude,
            user_longitude=self.longitude,
            options=options,
        )

    def _distance(self, offer: ConnectorOffer) -> float:
        return haversine_km(
            self.latitude, self.longitude, offer.agency.latitude, offer.agency.longitude
        )

    def _option(
        self,
        key: str,
        title: str,
        lines: list[CartLine],
        products: dict[int, ProductRead],
        offers: list[ConnectorOffer],
    ) -> ComparisonOption:
        available, unavailable, agencies = [], [], {}
        for line in lines:
            product = products[line.product_id]
            candidates = [
                o
                for o in offers
                if o.product_id == line.product_id
                and o.stock >= required_packs(line.quantity, o.reference_quantity)
            ]
            if not candidates:
                unavailable.append(
                    UnavailableLine(
                        product_id=product.id,
                        product_name=product.name,
                        quantity=line.quantity,
                    )
                )
                continue
            # Prix réellement payé, puis distance/délai/référence pour départager sans hasard.
            offer = min(
                candidates,
                key=lambda o: (
                    line_cost(o, line.quantity),
                    self._distance(o),
                    o.preparation_minutes,
                    o.supplier,
                    o.agency.id,
                    o.supplier_reference,
                ),
            )
            packs = required_packs(line.quantity, offer.reference_quantity)
            available.append(
                SelectedLine(
                    product_id=product.id,
                    product_name=product.name,
                    reference_unit=product.reference_unit,
                    requested_quantity=line.quantity,
                    purchased_quantity=packs * offer.reference_quantity,
                    packs=packs,
                    supplier=offer.supplier,
                    supplier_reference=offer.supplier_reference,
                    supplier_unit=offer.supplier_unit,
                    agency_id=offer.agency.id,
                    pack_price=offer.price,
                    line_total=line_cost(offer, line.quantity),
                    available_quantity=offer.available_quantity,
                    preparation_minutes=offer.preparation_minutes,
                    updated_at=offer.updated_at,
                )
            )
            agencies[offer.agency.id] = AgencyResult(
                id=offer.agency.id,
                supplier=offer.supplier,
                name=offer.agency.name,
                address=offer.agency.address,
                postal_code=offer.agency.postal_code,
                city=offer.agency.city,
                distance_km=round(self._distance(offer), 2),
            )
        subtotal = sum((line.line_total for line in available), Decimal("0.00"))
        return ComparisonOption(
            key=key,
            title=title,
            valid=not unavailable,
            total=subtotal if not unavailable else None,
            available_subtotal=subtotal,
            available=available,
            unavailable=unavailable,
            supplier_count=len({line.supplier for line in available}),
            agency_count=len(agencies),
            max_preparation_minutes=max(
                (line.preparation_minutes for line in available), default=0
            ),
            agencies=sorted(agencies.values(), key=lambda a: (a.distance_km, a.id)),
        )
