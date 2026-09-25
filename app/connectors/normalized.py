"""DTO normalisés — indépendants du JSON brut d'un fournisseur ou d'un fichier."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class NormalizedAgency(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_id: str
    name: str | None = None
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class NormalizedSupplierProduct(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_reference: str
    name: str
    brand: str | None = None
    supplier_unit: str | None = None
    packaging_quantity: Decimal | None = None
    reference_unit: str | None = None
    reference_quantity: Decimal | None = None
    ean: str | None = None
    product_code: str | None = None  # mapping explicite PMC (optionnel)
    image_url: str | None = None  # http(s) uniquement


class NormalizedOffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    supplier: str
    agency: NormalizedAgency
    product: NormalizedSupplierProduct
    # None = ligne catalogue sans prix (pas d'offre active).
    price: Decimal | None = Field(default=None, ge=0)
    currency: str
    tax_basis: str
    vat_rate: Decimal | None = None
    available_quantity: Decimal | None = None
    preparation_minutes: int | None = None
    observed_at: datetime | None = None
    source_url: str | None = None
    seller: str | None = None
    verification_status: str | None = None
