from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CartLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, le=1000000, max_digits=10, decimal_places=3)


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[CartLine] = Field(min_length=1, max_length=100)

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
    distance_km: float


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


class ComparisonResponse(BaseModel):
    currency: str = "EUR"
    tax_basis: str = "HT"
    simulated: bool = True
    user_latitude: float
    user_longitude: float
    options: list[ComparisonOption]
