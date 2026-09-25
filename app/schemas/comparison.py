from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.location import OriginRequest, ResolvedOrigin, RouteResult
from app.services.procurement_cost import CostBreakdown, CostParameters


class CartLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, le=1000000, max_digits=10, decimal_places=3)


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[CartLine] = Field(min_length=1, max_length=100)
    origin: OriginRequest | None = None
    # Base fiscale demandée ; si absente, déduite des offres (HT préféré si mixte).
    tax_basis: str | None = Field(default=None, pattern="^(HT|TTC)$")

    @model_validator(mode="after")
    def no_duplicates(self):
        ids = [line.product_id for line in self.lines]
        if len(ids) != len(set(ids)):
            raise ValueError("Regroupez les quantités d'un produit sur une seule ligne.")
        return self


class AgencyResult(BaseModel):
    id: int
    supplier: str
    name: str
    address: str
    postal_code: str
    city: str
    # None = catalogue national / agence non géolocalisée (pas de distance fictive).
    distance_km: float | None = None
    # False pour catalogue national synthétique (ne compte pas comme arrêt physique).
    is_geolocated: bool = True


class SelectedLine(BaseModel):
    product_id: int
    product_name: str
    reference_unit: str
    requested_quantity: Decimal
    purchased_quantity: Decimal
    packs: int
    supplier: str
    supplier_reference: str
    supplier_unit: str
    agency_id: int
    pack_price: Decimal
    line_total: Decimal
    available_quantity: Decimal
    preparation_minutes: int
    updated_at: datetime
    tax_basis: str = "HT"
    image_url: str | None = None
    # Conditionnement historique (snapshot) : taille d'un pack vendu.
    packaging_quantity: Decimal = Decimal("1")
    reference_quantity: Decimal = Decimal("1")
    # Prix source fournisseur (historique) — optionnel pour snapshots anciens.
    source_price: Decimal | None = None
    source_tax_basis: str | None = None
    vat_rate: Decimal | None = None


class UnavailableLine(BaseModel):
    product_id: int
    product_name: str
    quantity: Decimal
    reason: str = "Aucune offre active avec un stock suffisant dans une même agence."


class ComparisonOption(BaseModel):
    key: str
    title: str
    valid: bool
    total: Decimal | None
    available_subtotal: Decimal
    available: list[SelectedLine]
    unavailable: list[UnavailableLine]
    supplier_count: int
    agency_count: int
    max_preparation_minutes: int
    agencies: list[AgencyResult]


class ProcurementStrategy(BaseModel):
    key: str
    title: str
    valid: bool
    explanation: str
    material_total: Decimal | None = None
    estimated_procurement_cost: Decimal | None = None
    stops: list[AgencyResult] = Field(default_factory=list)
    supplier_count: int = 0
    total_distance_km: Decimal | None = None
    travel_minutes: Decimal | None = None
    max_preparation_minutes: int = 0
    total_minutes: Decimal | None = None
    route: RouteResult | None = None
    lines: list[SelectedLine] = Field(default_factory=list)
    unavailable: list[UnavailableLine] = Field(default_factory=list)
    cost_breakdown: CostBreakdown | None = None


class StrategyDelta(BaseModel):
    baseline: str = "single_stop"
    compared: str = "minimum_materials"
    material_savings: Decimal
    extra_distance_km: Decimal
    extra_travel_minutes: Decimal
    extra_total_minutes: Decimal


class ComparisonResponse(BaseModel):
    currency: str = "EUR"
    tax_basis: str = "HT"
    simulated: bool = True
    user_latitude: float
    user_longitude: float
    options: list[ComparisonOption]
    origin: ResolvedOrigin | None = None
    cost_parameters: CostParameters | None = None
    strategies: list[ProcurementStrategy] = Field(default_factory=list)
    minimum_vs_single: StrategyDelta | None = None
    # Offres exclues car base fiscale incompatible avec le comparatif retenu.
    excluded_incompatible_tax_basis: int = 0
    tax_basis_note: str | None = None
