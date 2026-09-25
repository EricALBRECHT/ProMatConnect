from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class AgencyData(BaseModel):
    """Agence magasin (coords) ou point national de catalogue (coords absentes)."""

    model_config = ConfigDict(frozen=True)
    id: int
    name: str
    address: str
    postal_code: str
    city: str
    latitude: float | None = None
    longitude: float | None = None

    @model_validator(mode="after")
    def _coords_bounds(self):
        if self.latitude is not None and not (-90 <= self.latitude <= 90):
            raise ValueError("latitude hors bornes")
        if self.longitude is not None and not (-180 <= self.longitude <= 180):
            raise ValueError("longitude hors bornes")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude et longitude doivent être toutes deux renseignées ou absentes")
        return self

    @property
    def is_geolocated(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class ConnectorOffer(BaseModel):
    """Prix d'un conditionnement ; available_quantity en unités de référence PMC.

    price = prix source fournisseur (jamais écrasé par une conversion).
    tax_basis = HT | TTC de ce prix source.
    vat_rate = taux % connu (None = pas de conversion vers l'autre base).
    """

    model_config = ConfigDict(frozen=True)
    supplier: str
    agency: AgencyData
    product_id: int
    supplier_reference: str
    supplier_unit: str
    reference_quantity: Decimal = Field(gt=0)
    reference_unit: str | None = None
    packaging_quantity: Decimal = Field(default=Decimal("1"), gt=0)
    price: Decimal = Field(ge=0)
    tax_basis: str = "HT"
    vat_rate: Decimal | None = None
    currency: str = "EUR"
    stock: int = Field(ge=0)
    preparation_minutes: int = Field(ge=0)
    updated_at: datetime
    image_url: str | None = None

    @computed_field
    @property
    def available_quantity(self) -> Decimal:
        # Catalogue national / prix sans stock magasin : stock 0 ≠ rupture.
        if self.stock <= 0 and not self.agency.is_geolocated:
            return Decimal("1000000")
        return self.reference_quantity * self.stock

    def covers_packs(self, packs: int) -> bool:
        if self.stock <= 0 and not self.agency.is_geolocated:
            return True
        return self.stock >= packs


class ConnectorHealth(BaseModel):
    model_config = ConfigDict(frozen=True)
    ok: bool
    connector_key: str
    supplier_key: str
    source_type: str
    detail: str | None = None


class SupplierConnector(ABC):
    """Interface minimale : démo, import fichier, future API fournisseur.

    `supplier_name` reste le contrat historique du comparateur.
    Les propriétés connector_key / source_type ont des défauts pour les stubs de test.
    """

    @property
    @abstractmethod
    def supplier_name(self) -> str:
        """Nom fournisseur catalogue (clé utilisée par OfferRepository)."""

    @property
    def supplier_key(self) -> str:
        return self.supplier_name

    @property
    def connector_key(self) -> str:
        return f"demo:{self.supplier_key}"

    @property
    def display_name(self) -> str:
        return self.supplier_key

    @property
    def source_type(self) -> str:
        return "demo"

    @abstractmethod
    def get_offers(self, product_ids: list[int]) -> list[ConnectorOffer]:
        """Résout les références PMC mappées et retourne les offres de toutes les agences."""

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            ok=True,
            connector_key=self.connector_key,
            supplier_key=self.supplier_key,
            source_type=self.source_type,
        )
