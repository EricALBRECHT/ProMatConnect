from decimal import ROUND_CEILING, Decimal

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
from app.services.tax import (
    CENT,
    CONVERSION_UNAVAILABLE_MSG,
    can_compare_in_basis,
    convert_amount,
    display_converted,
    money_round,
    resolve_comparison_basis,
)


def required_packs(quantity: Decimal, reference_quantity: Decimal) -> int:
    """Nombre entier de conditionnements nécessaires (plafond)."""
    return int((quantity / reference_quantity).to_integral_value(rounding=ROUND_CEILING))


def line_cost(
    offer: ConnectorOffer,
    quantity: Decimal,
    *,
    compare_basis: str | None = None,
) -> Decimal:
    """Coût d'achat = packs × prix conditionnement, dans la base de comparaison.

    Conversion fiscale sur le total source (packs × price) avant arrondi monétaire.
    Si compare_basis est None : prix source tel quel (rétrocompat).
    """
    packs = required_packs(quantity, offer.reference_quantity)
    source_total = offer.price * packs
    if compare_basis is None:
        return money_round(source_total)
    converted = convert_amount(
        source_total, offer.tax_basis or "HT", offer.vat_rate, compare_basis
    )
    if converted is None:
        raise ValueError(CONVERSION_UNAVAILABLE_MSG)
    return money_round(converted)


def resolve_tax_basis(
    offers: list[ConnectorOffer], requested: str | None
) -> tuple[str, list[ConnectorOffer], int, str | None]:
    """Retient les offres comparables dans une base de comparaison explicite.

    Une offre est gardée si sa base source = base demandée, ou si vat_rate
    est connu (conversion possible). Jamais de taux 20 % implicite.
    """
    basis = resolve_comparison_basis(requested)
    if not offers:
        return basis, [], 0, None

    kept: list[ConnectorOffer] = []
    excluded = 0
    for offer in offers:
        if can_compare_in_basis(offer.tax_basis or "HT", offer.vat_rate, basis):
            kept.append(offer)
        else:
            excluded += 1

    note = None
    if excluded:
        note = (
            f"{excluded} offre(s) exclue(s) : {CONVERSION_UNAVAILABLE_MSG} "
            f"(comparatif {basis})."
        )
    return basis, kept, excluded, note


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
        tax_basis: str | None = None,
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
        self.requested_tax_basis = tax_basis

    def compare(
        self, lines: list[CartLine], products: dict[int, ProductRead]
    ) -> ComparisonResponse:
        ids = [line.product_id for line in lines]
        raw_offers = [
            offer for connector in self.connectors for offer in connector.get_offers(ids)
        ]
        tax_basis, offers, excluded, note = resolve_tax_basis(
            raw_offers, self.requested_tax_basis
        )
        options = [
            self._option(
                f"supplier_{index}",
                f"Tout chez {connector.supplier_name}",
                lines,
                products,
                [offer for offer in offers if offer.supplier == connector.supplier_name],
                tax_basis,
            )
            for index, connector in enumerate(self.connectors, start=1)
        ]
        options.append(
            self._option("optimized", "Panier optimisé", lines, products, offers, tax_basis)
        )
        strategies, delta = ProcurementOptimizer(
            self.routing,
            self.order_optimizer,
            ProcurementCostService(self.cost_parameters),
            self.max_agencies,
        ).optimize(
            lines,
            offers,
            self.origin,
            lambda selected: self._option(
                "candidate", "", lines, products, selected, tax_basis
            ),
        )
        return ComparisonResponse(
            currency="EUR",
            tax_basis=tax_basis,
            origin=self.origin,
            cost_parameters=self.cost_parameters,
            strategies=strategies,
            minimum_vs_single=delta,
            user_latitude=self.latitude,
            user_longitude=self.longitude,
            options=options,
            excluded_incompatible_tax_basis=excluded,
            tax_basis_note=note,
        )

    def _distance(self, offer: ConnectorOffer) -> float:
        if not offer.agency.is_geolocated:
            return float("inf")
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
        tax_basis: str,
    ) -> ComparisonOption:
        available, unavailable, agencies = [], [], {}
        for line in lines:
            product = products[line.product_id]
            candidates = [
                o
                for o in offers
                if o.product_id == line.product_id
                and o.covers_packs(required_packs(line.quantity, o.reference_quantity))
                and can_compare_in_basis(o.tax_basis or "HT", o.vat_rate, tax_basis)
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
            offer = min(
                candidates,
                key=lambda o: (
                    line_cost(o, line.quantity, compare_basis=tax_basis),
                    self._distance(o),
                    o.preparation_minutes,
                    o.supplier,
                    o.agency.id,
                    o.supplier_reference,
                ),
            )
            packs = required_packs(line.quantity, offer.reference_quantity)
            ref_unit = offer.reference_unit or product.reference_unit
            converted_pack = display_converted(
                offer.price, offer.tax_basis or "HT", offer.vat_rate, tax_basis
            )
            pack_price = (
                converted_pack if converted_pack is not None else money_round(offer.price)
            )
            available.append(
                SelectedLine(
                    product_id=product.id,
                    product_name=product.name,
                    reference_unit=ref_unit,
                    requested_quantity=line.quantity,
                    purchased_quantity=packs * offer.reference_quantity,
                    packs=packs,
                    supplier=offer.supplier,
                    supplier_reference=offer.supplier_reference,
                    supplier_unit=offer.supplier_unit,
                    agency_id=offer.agency.id,
                    pack_price=pack_price,
                    line_total=line_cost(offer, line.quantity, compare_basis=tax_basis),
                    available_quantity=offer.available_quantity,
                    preparation_minutes=offer.preparation_minutes,
                    updated_at=offer.updated_at,
                    tax_basis=tax_basis,
                    image_url=offer.image_url,
                    packaging_quantity=offer.packaging_quantity,
                    reference_quantity=offer.reference_quantity,
                    source_price=offer.price,
                    source_tax_basis=offer.tax_basis or "HT",
                    vat_rate=offer.vat_rate,
                )
            )
            distance = None if not offer.agency.is_geolocated else round(self._distance(offer), 2)
            agencies[offer.agency.id] = AgencyResult(
                id=offer.agency.id,
                supplier=offer.supplier,
                name=offer.agency.name,
                address=offer.agency.address,
                postal_code=offer.agency.postal_code,
                city=offer.agency.city,
                distance_km=distance,
                is_geolocated=offer.agency.is_geolocated,
                is_national_catalog=offer.agency.is_national_catalog,
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
            agencies=sorted(
                agencies.values(),
                key=lambda a: (
                    a.distance_km is None,
                    a.distance_km if a.distance_km is not None else 0.0,
                    a.id,
                ),
            ),
        )
