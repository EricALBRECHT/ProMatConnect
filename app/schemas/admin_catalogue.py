"""View models back-office catalogue ProMatConnect (/admin/catalogue)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CatalogueOfferView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: int
    agency_id: int
    agency_name: str
    price: Decimal
    tax_basis: str
    vat_rate: Decimal | None = None
    price_ht: Decimal | None = None
    price_ttc: Decimal | None = None
    currency: str = "EUR"
    stock: int = 0
    source_type: str | None = None
    catalog_id: int | None = None
    source_url: str | None = None
    observed_at: str | None = None


class CatalogueSupplierProductView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_product_id: int
    supplier: str
    supplier_reference: str
    designation: str
    brand: str | None = None
    ean: str | None = None
    image_url: str | None = None
    supplier_unit: str
    packaging_quantity: Decimal
    reference_unit: str | None = None
    reference_quantity: Decimal
    correction_source: str | None = None
    unit_compatible: bool | None = None
    unit_anomaly: bool = False
    active: bool = True
    offers: list[CatalogueOfferView] = Field(default_factory=list)
    min_price_ht: Decimal | None = None
    min_price_ttc: Decimal | None = None
    catalog_id: int | None = None
    introduced_by_catalog_id: int | None = None
    source_url: str | None = None
    observed_at: str | None = None


class CatalogueProductListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    code: str
    name: str
    category: str
    subcategory: str | None = None
    reference_unit: str
    is_active: bool = True
    is_legacy: bool = False
    image_url: str | None = None
    attributes: dict | None = None
    supplier_product_count: int = 0
    offer_count: int = 0
    anomaly_count: int = 0
    min_price_ht: Decimal | None = None
    min_price_ttc: Decimal | None = None
    has_price: bool = False


class CatalogueProductListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CatalogueProductListItem]
    total: int
    page: int
    page_size: int
    pages: int
    categories: list[str] = Field(default_factory=list)
    unmapped_count: int = 0
    anomaly_product_count: int = 0
    legacy_count: int = 0


class CatalogueProductDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    code: str
    name: str
    category: str
    subcategory: str | None = None
    reference_unit: str
    description: str | None = None
    attributes: dict | None = None
    is_active: bool = True
    is_legacy: bool = False
    image_url: str | None = None
    chantier_usage_count: int = 0
    can_edit_reference_unit: bool = True
    supplier_products: list[CatalogueSupplierProductView] = Field(default_factory=list)
    min_price_ht: Decimal | None = None
    min_price_ttc: Decimal | None = None
    offer_count: int = 0
    anomaly_count: int = 0
    allowed_reference_units: list[str] = Field(default_factory=list)


class CatalogueUnmappedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_product_id: int
    supplier: str
    supplier_reference: str
    designation: str
    brand: str | None = None
    ean: str | None = None
    image_url: str | None = None
    supplier_unit: str
    packaging_quantity: Decimal | None = None
    reference_unit: str | None = None
    reference_quantity: Decimal | None = None
    price: Decimal | None = None
    tax_basis: str | None = None
    vat_rate: Decimal | None = None
    catalog_id: int | None = None
    source_url: str | None = None
    observed_at: str | None = None
    price_ht: Decimal | None = None
    price_ttc: Decimal | None = None


class CatalogueUnmappedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CatalogueUnmappedItem]
    total: int
    page: int
    page_size: int
    pages: int


class CatalogueProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=2, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    subcategory: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    attributes: dict | None = None
    is_active: bool | None = None
    # reference_unit géré via PATCH dédié (verrou usage chantier).
