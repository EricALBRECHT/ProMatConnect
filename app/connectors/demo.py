"""Connecteur démo / catalogue interne — lit les offres déjà persistées en base."""

from __future__ import annotations

from app.connectors.base import ConnectorOffer, SupplierConnector
from app.repositories.offers import OfferRepository


class DemoSupplierConnector(SupplierConnector):
    """Alimente le comparateur depuis le catalogue interne (seed démo ou import)."""

    def __init__(
        self,
        repository: OfferRepository,
        supplier_name: str,
        *,
        source_type: str = "demo",
        connector_prefix: str = "demo",
    ):
        self.repository = repository
        self._supplier_name = supplier_name
        self._source_type = source_type
        self._connector_prefix = connector_prefix

    @property
    def connector_key(self) -> str:
        return f"{self._connector_prefix}:{self._supplier_name}"

    @property
    def supplier_key(self) -> str:
        return self._supplier_name

    @property
    def supplier_name(self) -> str:
        return self._supplier_name

    @property
    def display_name(self) -> str:
        return self._supplier_name

    @property
    def source_type(self) -> str:
        return self._source_type

    def get_offers(self, product_ids: list[int]) -> list[ConnectorOffer]:
        return self.repository.for_supplier(self.supplier_key, product_ids)
