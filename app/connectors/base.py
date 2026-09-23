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


class SupplierConnector(ABC):
    @property
    @abstractmethod
    def supplier_name(self) -> str: ...

    @abstractmethod
    def get_offers(self, product_ids: list[int]) -> list[ConnectorOffer]:
        """Résout les références PMC et retourne les offres de toutes les agences."""
        ...
