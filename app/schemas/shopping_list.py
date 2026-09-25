"""View models pour la liste d'achat (dérivés du snapshot approvisionnement)."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ShoppingListLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    product_name: str
    supplier_reference: str
    reference_unit: str
    supplier_unit: str
    requested_quantity: Decimal
    purchased_quantity: Decimal
    packs: int
    pack_size: Decimal | None = None
    pack_price: Decimal
    line_total: Decimal
    preparation_minutes: int | None = None
    available_quantity: Decimal | None = None


class ShoppingListStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agency_id: int
    supplier: str
    name: str
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    distance_km: float | None = None
    stop_order: int
    travel_minutes_from_previous: float | None = None
    lines: list[ShoppingListLine] = Field(default_factory=list)
    subtotal: Decimal


class ShoppingListTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_total: Decimal | None = None
    distance_cost: Decimal | None = None
    time_cost: Decimal | None = None
    extra_stops_cost: Decimal | None = None
    estimated_procurement_cost: Decimal | None = None
    total_distance_km: Decimal | None = None
    travel_minutes: Decimal | None = None


class ShoppingListChantier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    nom: str
    client: str | None = None
    adresse: str
    date_prevue: str | None = None


class ShoppingListRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool
    obsolete: bool = False
    chantier: ShoppingListChantier
    strategy_key: str | None = None
    strategy_title: str | None = None
    chosen_at: datetime | None = None
    currency: str = "EUR"
    tax_basis: str = "HT"
    stores: list[ShoppingListStore] = Field(default_factory=list)
    totals: ShoppingListTotals = Field(default_factory=ShoppingListTotals)
    line_count: int = 0
    price_disclaimer: str = "Prix constatés lors de la comparaison"
