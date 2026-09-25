from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class AgencyData(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: int
    name: str
    address: str
    postal_code: str
    city: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ConnectorOffer(BaseModel):
    """Prix HT et stock en conditionnements ; available_quantity en unités PMC."""

    model_config = ConfigDict(frozen=True)
    supplier: str
    agency: AgencyData
    product_id: int
    supplier_reference: str
    supplier_unit: str
    reference_quantity: Decimal = Field(gt=0)
    price: Decimal = Field(ge=0)
    stock: int = Field(ge=0)
    preparation_minutes: int = Field(ge=0)
    updated_at: datetime

    @computed_field
    @property
    def available_quantity(self) -> Decimal:
        return self.reference_quantity * self.stock


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
