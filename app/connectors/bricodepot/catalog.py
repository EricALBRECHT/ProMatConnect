"""Facade catalogue Brico Dépôt : sitemap → Magento → DTO."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.connectors.bricodepot.catalog_dto import (
    BricoDepotCatalogProduct,
    BricoDepotDiscoveredProduct,
)
from app.connectors.bricodepot.catalog_enrich import BricoDepotCatalogEnricher
from app.connectors.bricodepot.catalog_sample import (
    BricoDepotDiverseSampler,
    DiverseSampleResult,
)
from app.connectors.bricodepot.catalog_sitemap import (
    BricoDepotSitemapDiscoverer,
    BricoDepotSitemapError,
)
from app.connectors.bricodepot.client import BricoDepotClient, PRODUCT_SKU_PAGE_SIZE

# Plafond de sécurité phase 2 — le catalogue complet (~27k) reste interdit.
VALIDATION_IMPORT_MAX = 500


@dataclass
class BricoDepotCatalogPipelineResult:
    discovered: list[BricoDepotDiscoveredProduct] = field(default_factory=list)
    products: list[BricoDepotCatalogProduct] = field(default_factory=list)
    missing_skus: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sample_mode: str | None = None
    family_inventory: list[dict] = field(default_factory=list)
    family_by_sku: dict[str, str] = field(default_factory=dict)
    selection_counts: dict[str, int] = field(default_factory=dict)
    graphql_calls_sample: int = 0

    @property
    def enriched_count(self) -> int:
        missing = set(self.missing_skus)
        return sum(1 for p in self.products if p.supplier_reference not in missing)


class BricoDepotCatalogService:
    """Orchestration discovery + enrichissement (aucun accès DB)."""

    def __init__(
        self,
        *,
        discoverer: BricoDepotSitemapDiscoverer | None = None,
        enricher: BricoDepotCatalogEnricher | None = None,
        sampler: BricoDepotDiverseSampler | None = None,
        client: BricoDepotClient | None = None,
        seller_id: str | int = "10",
    ):
        self._client = client
        self.discoverer = discoverer or BricoDepotSitemapDiscoverer()
        if enricher is not None:
            self.enricher = enricher
        else:
            self.enricher = BricoDepotCatalogEnricher(
                client or self._ensure_client(),
                seller_id=seller_id,
                batch_size=PRODUCT_SKU_PAGE_SIZE,
            )
        if sampler is not None:
            self.sampler = sampler
        else:
            self.sampler = BricoDepotDiverseSampler(
                client or self._ensure_client(),
                seller_id=seller_id,
            )
        self.seller_id = seller_id

    def _ensure_client(self) -> BricoDepotClient:
        if self._client is None:
            self._client = BricoDepotClient()
        return self._client

    def discover_products(
        self, *, limit: int | None = None
    ) -> list[BricoDepotDiscoveredProduct]:
        return self.discoverer.discover_products(limit=limit)

    def build_catalog(
        self,
        *,
        limit: int | None = None,
        sample_diverse: bool = False,
    ) -> BricoDepotCatalogPipelineResult:
        result = BricoDepotCatalogPipelineResult()
        if sample_diverse:
            if limit is None:
                result.errors.append("sample_diverse exige limit")
                return result
            return self._build_diverse_catalog(limit=limit)

        try:
            result.discovered = self.discover_products(limit=limit)
        except BricoDepotSitemapError as exc:
            result.errors.append(f"sitemap: {exc}")
            return result
        return self._enrich_discovered(result)

    def _build_diverse_catalog(self, *, limit: int) -> BricoDepotCatalogPipelineResult:
        result = BricoDepotCatalogPipelineResult(sample_mode="diverse_round_robin")
        sample: DiverseSampleResult = self.sampler.sample_round_robin(limit=limit)
        result.errors.extend(sample.errors)
        result.family_inventory = list(sample.family_inventory)
        result.family_by_sku = dict(sample.family_by_sku)
        result.selection_counts = dict(sample.selection_counts)
        result.graphql_calls_sample = sample.graphql_calls
        result.discovered = list(sample.selected)
        if not result.discovered:
            if not result.errors:
                result.errors.append("échantillon diversifié vide")
            return result
        return self._enrich_discovered(result)

    def _enrich_discovered(
        self, result: BricoDepotCatalogPipelineResult
    ) -> BricoDepotCatalogPipelineResult:
        try:
            products, missing = self.enricher.enrich(result.discovered)
            result.products = products
            result.missing_skus = missing
        except Exception as exc:  # noqa: BLE001 — surface erreurs HTTP/GraphQL
            result.errors.append(f"magento: {exc}")
            from app.connectors.bricodepot.catalog_enrich import discovered_only_product

            result.products = [discovered_only_product(d) for d in result.discovered]
            result.missing_skus = [d.supplier_reference for d in result.discovered]
        return result
