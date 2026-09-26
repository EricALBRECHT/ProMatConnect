"""Brico Dépôt — client HTTP + connecteur LIVE."""

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
    "SUPPLIER_NAME",
    "SUPPLIER_NAME_ALIASES",
    "is_brico_supplier_name",
]
