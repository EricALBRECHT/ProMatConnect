from decimal import ROUND_CEILING, Decimal

from app.connectors.base import ConnectorOffer, SupplierConnector, resolve_agency_key
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
    return packs_cost(
        offer,
        required_packs(quantity, offer.reference_quantity),
        compare_basis=compare_basis,
    )


def packs_cost(
    offer: ConnectorOffer,
    packs: int,
    *,
    compare_basis: str | None = None,
) -> Decimal:
    """Coût de `packs` conditionnements, sans inventer de prix."""
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

    def _offer_rank(
        self,
        offer: ConnectorOffer,
        quantity: Decimal,
        tax_basis: str,
        *,
        packs: int | None = None,
    ) -> tuple:
        bought = packs if packs is not None else required_packs(quantity, offer.reference_quantity)
        return (
            packs_cost(offer, bought, compare_basis=tax_basis),
            self._distance(offer),
            offer.preparation_minutes,
            offer.supplier,
            resolve_agency_key(agency_key=offer.agency.agency_key, agency_id=offer.agency.id),
            offer.supplier_reference,
        )

    def _selected_line(
        self,
        offer: ConnectorOffer,
        product: ProductRead,
        quantity: Decimal,
        tax_basis: str,
        *,
        availability: str,
    ) -> SelectedLine:
        needed = required_packs(quantity, offer.reference_quantity)
        packs = min(offer.stock, needed) if availability == "partial" else needed
        purchased = packs * offer.reference_quantity
        missing = quantity - purchased
        if missing < 0:
            missing = Decimal("0")
        ref_unit = offer.reference_unit or product.reference_unit
        converted_pack = display_converted(
            offer.price, offer.tax_basis or "HT", offer.vat_rate, tax_basis
        )
        pack_price = converted_pack if converted_pack is not None else money_round(offer.price)
        return SelectedLine(
            product_id=product.id,
            product_name=product.name,
            reference_unit=ref_unit,
            requested_quantity=quantity,
            purchased_quantity=purchased,
            packs=packs,
            supplier=offer.supplier,
            supplier_reference=offer.supplier_reference,
            supplier_unit=offer.supplier_unit,
            agency_id=offer.agency.id,
            agency_key=resolve_agency_key(
                agency_key=offer.agency.agency_key, agency_id=offer.agency.id
            ),
            pack_price=pack_price,
            line_total=packs_cost(offer, packs, compare_basis=tax_basis),
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
            live_status=offer.live_status,
            fetched_at=offer.fetched_at,
            availability=availability,
            missing_quantity=missing if availability == "partial" else None,
        )

    def _remember_agency(self, agencies: dict, offer: ConnectorOffer) -> None:
        agency_key = resolve_agency_key(
            agency_key=offer.agency.agency_key, agency_id=offer.agency.id
        )
        distance = None if not offer.agency.is_geolocated else round(self._distance(offer), 2)
        agencies[agency_key] = AgencyResult(
            id=offer.agency.id,
            agency_key=agency_key,
            supplier=offer.supplier,
            name=offer.agency.name,
            address=offer.agency.address,
            postal_code=offer.agency.postal_code,
            city=offer.agency.city,
            distance_km=distance,
            is_geolocated=offer.agency.is_geolocated,
            is_national_catalog=offer.agency.is_national_catalog,
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
            comparable = [
                o
                for o in offers
                if o.product_id == line.product_id
                and can_compare_in_basis(o.tax_basis or "HT", o.vat_rate, tax_basis)
            ]
            delayed = [o for o in comparable if (o.fulfillment or "") == "delayed"]
            on_order = [o for o in comparable if (o.fulfillment or "") == "order_only"]
            blocked = [o for o in comparable if (o.fulfillment or "") == "unavailable"]
            stock_offers = [
                o
                for o in comparable
                if (o.fulfillment or "") not in {"delayed", "order_only", "unavailable"}
            ]
            full = [
                o
                for o in stock_offers
                if o.covers_packs(required_packs(line.quantity, o.reference_quantity))
            ]
            partial = [
                o
                for o in stock_offers
                if o.stock > 0
                and not o.covers_packs(required_packs(line.quantity, o.reference_quantity))
            ]
            if full:
                offer = min(full, key=lambda o: self._offer_rank(o, line.quantity, tax_basis))
                available.append(
                    self._selected_line(
                        offer, product, line.quantity, tax_basis, availability="available"
                    )
                )
                self._remember_agency(agencies, offer)
                continue
            if partial:
                offer = min(
                    partial,
                    key=lambda o: (
                        -min(o.stock, required_packs(line.quantity, o.reference_quantity)),
                        *self._offer_rank(
                            o,
                            line.quantity,
                            tax_basis,
                            packs=min(o.stock, required_packs(line.quantity, o.reference_quantity)),
                        ),
                    ),
                )
                available.append(
                    self._selected_line(
                        offer, product, line.quantity, tax_basis, availability="partial"
                    )
                )
                self._remember_agency(agencies, offer)
                continue
            if delayed or on_order:
                pool = delayed or on_order
                kind = "delayed" if delayed else "order_only"
                offer = min(pool, key=lambda o: self._offer_rank(o, line.quantity, tax_basis))
                available.append(
                    self._selected_line(
                        offer, product, line.quantity, tax_basis, availability=kind
                    )
                )
                self._remember_agency(agencies, offer)
                continue
            if blocked and not stock_offers:
                unavailable.append(
                    UnavailableLine(
                        product_id=product.id,
                        product_name=product.name,
                        quantity=line.quantity,
                        availability="unavailable",
                        reason="Indisponible",
                    )
                )
            elif comparable:
                unavailable.append(
                    UnavailableLine(
                        product_id=product.id,
                        product_name=product.name,
                        quantity=line.quantity,
                        availability="out_of_stock",
                        reason="Indisponible dans ce dépôt",
                    )
                )
            else:
                unavailable.append(
                    UnavailableLine(
                        product_id=product.id,
                        product_name=product.name,
                        quantity=line.quantity,
                        availability="unavailable",
                        reason="Aucune offre exploitable pour ce produit.",
                    )
                )
        subtotal = sum((line.line_total for line in available), Decimal("0.00"))
        requested = len(lines)
        n_partial = sum(1 for line in available if line.availability == "partial")
        n_later = sum(
            1 for line in available if line.availability in {"delayed", "order_only"}
        )
        n_available = len(available) - n_partial - n_later
        n_unavailable = len(unavailable)
        complete = requested > 0 and n_available == requested
        coverage = (
            (Decimal(n_available) * Decimal("100") / Decimal(requested)).quantize(Decimal("0.01"))
            if requested
            else Decimal("0")
        )
        return ComparisonOption(
            key=key,
            title=title,
            valid=complete,
            total=subtotal if complete else None,
            available_subtotal=subtotal,
            available=available,
            unavailable=unavailable,
            lines_requested=requested,
            lines_available=n_available,
            lines_partial=n_partial,
            lines_unavailable=n_unavailable,
            coverage_rate=coverage,
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
                    a.agency_key or f"db:{a.id}",
                    a.id,
                ),
            ),
        )
