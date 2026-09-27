"""Enrichissement Magento GraphQL par lots de SKU (catalogue, hors live prix/stock)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.connectors.bricodepot.catalog_dto import (
    BricoDepotCatalogProduct,
    BricoDepotDiscoveredProduct,
)
from app.connectors.bricodepot.client import (
    PRODUCT_SKU_PAGE_SIZE,
    BricoDepotClient,
    DEFAULT_STORE_ID,
    _as_int,
    _as_str,
    _unwrap_payload,
    build_product_headers,
    normalize_skus,
)
from app.connectors.bricodepot.queries import load_query


def load_catalog_query() -> str:
    return load_query("catalog.graphql")


def chunk_skus(skus: list[str], size: int = PRODUCT_SKU_PAGE_SIZE) -> list[list[str]]:
    size = max(1, min(int(size), PRODUCT_SKU_PAGE_SIZE))
    return [skus[i : i + size] for i in range(0, len(skus), size)]


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def pick_category_path(categories: Any) -> str | None:
    """Choisit un url_path merchandising ; évite Top Produits / opérations."""
    if not isinstance(categories, list):
        return None
    scored: list[tuple[int, str]] = []
    fallback: list[tuple[int, str]] = []
    for entry in categories:
        if not isinstance(entry, dict):
            continue
        path = _as_str(entry.get("url_path"))
        if not path:
            continue
        depth = path.count("/")
        low = path.lower()
        if "opbdfr-top" in low or low.startswith("operation/") or "/op-" in low:
            fallback.append((depth, path))
            continue
        scored.append((depth, path))
    pool = scored or fallback
    if not pool:
        return None
    pool.sort(key=lambda t: t[0], reverse=True)
    return pool[0][1]


def pick_image_url(images: Any) -> str | None:
    if not isinstance(images, list):
        return None
    for img in images:
        if not isinstance(img, dict):
            continue
        url = _as_str(img.get("url"))
        if url and url.startswith(("http://", "https://")):
            return url
    return None


def parse_catalog_items_by_sku(
    payload: Any, *, requested_skus: list[str]
) -> dict[str, dict[str, Any]]:
    """Indexe les items bruts par SKU exact — ignore l'ordre de retour."""
    root = _unwrap_payload(payload)
    data = root.get("data", root)
    if not isinstance(data, dict):
        return {}
    products = data.get("products")
    if not isinstance(products, dict):
        return {}
    items = products.get("items") or []
    if not isinstance(items, list):
        return {}

    by_sku: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sku = _as_str(item.get("sku"))
        if sku and sku not in by_sku:
            by_sku[sku] = item

    wanted = normalize_skus(requested_skus)
    return {sku: by_sku[sku] for sku in wanted if sku in by_sku}


def item_to_catalog_product(
    item: dict[str, Any],
    *,
    discovered: BricoDepotDiscoveredProduct | None = None,
) -> BricoDepotCatalogProduct:
    sku = _as_str(item.get("sku")) or (
        discovered.supplier_reference if discovered else ""
    )
    return BricoDepotCatalogProduct(
        supplier_reference=sku,
        product_url=discovered.product_url if discovered else None,
        slug=discovered.slug if discovered else None,
        ean=_as_str(item.get("ean_code")) or _as_str(item.get("ean")),
        sap_code=_as_str(item.get("sap_easier_code")),
        name=_as_str(item.get("name")),
        brand=_as_str(item.get("brand")),
        image_url=pick_image_url(item.get("images")),
        packaging_label=_as_str(item.get("conditionnement_label")),
        content_net_value=_as_decimal(item.get("content_net_value")),
        content_net_unit=_as_str(item.get("content_net_unit_label"))
        or _as_str(item.get("content_net_unit")),
        category_path=pick_category_path(item.get("categories")),
        magento_product_id=_as_int(item.get("id")),
    )


def discovered_only_product(
    discovered: BricoDepotDiscoveredProduct,
) -> BricoDepotCatalogProduct:
    """DTO minimal si Magento n'a pas renvoyé le SKU."""
    return BricoDepotCatalogProduct(
        supplier_reference=discovered.supplier_reference,
        product_url=discovered.product_url,
        slug=discovered.slug,
    )


class BricoDepotCatalogEnricher:
    """Lots Magento ≤ PRODUCT_SKU_PAGE_SIZE, association par SKU exact."""

    def __init__(
        self,
        client: BricoDepotClient,
        *,
        catalog_query: str | None = None,
        batch_size: int = PRODUCT_SKU_PAGE_SIZE,
        seller_id: str | int = "10",
        store_id: str = DEFAULT_STORE_ID,
    ):
        self.client = client
        self._catalog_query = catalog_query
        self.batch_size = max(1, min(int(batch_size), PRODUCT_SKU_PAGE_SIZE))
        self.seller_id = str(seller_id)
        self.store_id = str(store_id)

    @property
    def catalog_query(self) -> str:
        if self._catalog_query is None:
            self._catalog_query = load_catalog_query()
        return self._catalog_query

    def fetch_raw_by_sku(self, skus: list[str]) -> dict[str, dict[str, Any]]:
        sku_list = normalize_skus(skus)
        merged: dict[str, dict[str, Any]] = {}
        for batch in chunk_skus(sku_list, self.batch_size):
            body = self.client._post(
                query=self.catalog_query,
                query_variables={
                    "filter": {"sku": {"in": batch}},
                    "currentPage": 1,
                    "pageSize": len(batch),
                },
                headers=build_product_headers(
                    seller_id=self.seller_id, store_id=self.store_id
                ),
            )
            merged.update(parse_catalog_items_by_sku(body, requested_skus=batch))
        return merged

    def enrich(
        self, discovered: list[BricoDepotDiscoveredProduct]
    ) -> tuple[list[BricoDepotCatalogProduct], list[str]]:
        """Retourne (produits DTO, SKU non enrichis)."""
        if not discovered:
            return [], []
        raw = self.fetch_raw_by_sku([d.supplier_reference for d in discovered])
        products: list[BricoDepotCatalogProduct] = []
        missing: list[str] = []
        for disc in discovered:
            item = raw.get(disc.supplier_reference)
            if item is None:
                missing.append(disc.supplier_reference)
                products.append(discovered_only_product(disc))
            else:
                products.append(item_to_catalog_product(item, discovered=disc))
        return products, missing
