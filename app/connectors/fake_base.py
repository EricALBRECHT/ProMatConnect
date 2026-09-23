from app.connectors.base import ConnectorOffer, SupplierConnector
from app.repositories.offers import OfferRepository


class DatabaseFakeConnector(SupplierConnector):
    """Simulateur local : lit uniquement les données de démonstration en base."""

    def __init__(self, repository: OfferRepository):
        self.repository = repository

    def get_offers(self, product_ids: list[int]) -> list[ConnectorOffer]:
        return self.repository.for_supplier(self.supplier_name, product_ids)
