"""Brico Dépôt — client HTTP + connecteur LIVE + catalogue sitemap."""

from app.connectors.bricodepot.catalog import (
    VALIDATION_IMPORT_MAX,
    BricoDepotCatalogService,
)
from app.connectors.bricodepot.catalog_dto import (
    BricoDepotCatalogProduct,
    BricoDepotDiscoveredProduct,
)
from app.connectors.bricodepot.client import BricoDepotClient, BricoDepotClientError
from app.connectors.bricodepot.connector import (
    BricoDepotConnector,
    SUPPLIER_NAME,
    SUPPLIER_NAME_ALIASES,
    is_brico_supplier_name,
)

__all__ = [
    "BricoDepotClient",
    "BricoDepotClientError",
    "BricoDepotConnector",
    "BricoDepotCatalogService",
    "BricoDepotCatalogProduct",
    "BricoDepotDiscoveredProduct",
    "VALIDATION_IMPORT_MAX",
    "SUPPLIER_NAME",
    "SUPPLIER_NAME_ALIASES",
    "is_brico_supplier_name",
]
