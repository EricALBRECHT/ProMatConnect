"""View models pour la liste d'achat (snapshot + suivi réel)."""

from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class ShoppingLineTracking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pris: bool = False
    quantite_reelle: Decimal | None = None
    prix_reel: Decimal | None = None
    sous_total_reel: Decimal | None = None
    ecart: Decimal | None = None
    renseigne: bool = False
    updated_at: datetime | None = None


class ShoppingListLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_key: str
    agency_id: int
    product_id: int
    product_name: str
    supplier_reference: str
    reference_unit: str
    supplier_unit: str
    requested_quantity: Decimal
    purchased_quantity: Decimal
    packs: int
    pack_size: Decimal | None = None
    packaging_quantity: Decimal | None = None
    reference_quantity: Decimal | None = None
    pack_price: Decimal
    line_total: Decimal
    preparation_minutes: int | None = None
    available_quantity: Decimal | None = None
    # pack_price = prix d'un pack (supplier_unit) ; packs = nombre de packs.
    price_unit_label: str = "pack"
    image_url: str | None = None
    suivi: ShoppingLineTracking = Field(default_factory=ShoppingLineTracking)
    # Snapshot fiscal — optionnel (snapshots anciens).
    tax_basis: str | None = None
    source_price: Decimal | None = None
    source_tax_basis: str | None = None
    vat_rate: Decimal | None = None


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


class ShoppingListActualTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines_renseignees: int = 0
    lines_total: int = 0
    material_total_renseigne: Decimal | None = None
    ecart_materiaux_renseignes: Decimal | None = None
    taken_count: int = 0


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
    approvisionnement_id: int | None = None
    snapshot_token: str | None = None
    strategy_key: str | None = None
    strategy_title: str | None = None
    chosen_at: datetime | None = None
    currency: str = "EUR"
    tax_basis: str = "HT"
    stores: list[ShoppingListStore] = Field(default_factory=list)
    totals: ShoppingListTotals = Field(default_factory=ShoppingListTotals)
    actual: ShoppingListActualTotals = Field(default_factory=ShoppingListActualTotals)
    line_count: int = 0
    taken_count: int = 0
    price_disclaimer: str = "Prix constatés lors de la comparaison"


class ShoppingLineTrackingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pris: bool
    quantite_reelle: Decimal | None = Field(default=None, ge=0, le=1000000, max_digits=10, decimal_places=3)
    prix_reel: Decimal | None = Field(default=None, ge=0, le=1000000, max_digits=12, decimal_places=2)
    updated_at: AwareDatetime | None = None

    @field_validator("quantite_reelle", "prix_reel", mode="before")
    @classmethod
    def empty_as_none(cls, value):
        if value == "" or value is None:
            return None
        return value


class ShoppingLineTrackingRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_key: str
    snapshot_token: str
    pris: bool
    quantite_reelle: Decimal | None
    prix_reel: Decimal | None
    sous_total_reel: Decimal | None
    ecart: Decimal | None
    renseigne: bool
    updated_at: datetime
    pack_price_prevu: Decimal | None = None
    packs_prevus: int | None = None
    line_total_prevu: Decimal | None = None
