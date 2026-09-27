"""DTO catalogue Brico Dépôt — indépendants de la DB."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class BricoDepotDiscoveredProduct:
    """URL sitemap produit normalisée."""

    supplier_reference: str
    product_url: str
    slug: str | None


@dataclass(frozen=True)
class BricoDepotCatalogProduct:
    """Référence catalogue enrichie (source sitemap + Magento).

    Les champs non garantis sont Optional. Aucune conversion m²→pièce.
    """

    supplier_reference: str
    product_url: str | None = None
    slug: str | None = None
    ean: str | None = None
    sap_code: str | None = None
    name: str | None = None
    brand: str | None = None
    image_url: str | None = None
    packaging_label: str | None = None
    content_net_value: Decimal | None = None
    content_net_unit: str | None = None
    category_path: str | None = None
    magento_product_id: int | None = None
