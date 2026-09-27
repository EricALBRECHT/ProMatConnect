"""Découverte catalogue complète Brico Dépôt — sitemaps produits (canonique).

Source robuste pour ~27k SKU uniques, ordre déterministe de première apparition.
Pas d'enrichissement GraphQL. Pas de Sensefuel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.connectors.bricodepot.catalog_dto import BricoDepotDiscoveredProduct
from app.connectors.bricodepot.catalog_sitemap import (
    BricoDepotSitemapDiscoverer,
    BricoDepotSitemapError,
    extract_locs,
    is_product_sitemap_url,
    parse_product_url,
)

SKU_RE = re.compile(r"^\d{8,14}$")


@dataclass
class CatalogDiscoveryReport:
    """Résultat découverte sans enrichissement."""

    products: list[BricoDepotDiscoveredProduct] = field(default_factory=list)
    discovered_total: int = 0
    duplicate_skus: int = 0
    invalid_skus: int = 0
    sitemap_url_count: int = 0
    product_sitemap_files: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "discovered_total": self.discovered_total,
            "duplicate_skus": self.duplicate_skus,
            "invalid_skus": self.invalid_skus,
            "sitemap_url_count": self.sitemap_url_count,
            "product_sitemap_files": self.product_sitemap_files,
            "errors": list(self.errors),
        }


class BricoDepotFullDiscoverer:
    """Découverte déterministe via sitemaps-produits-* uniquement."""

    def __init__(self, discoverer: BricoDepotSitemapDiscoverer | None = None):
        self.discoverer = discoverer or BricoDepotSitemapDiscoverer()

    def discover_all(self, *, limit: int | None = None) -> CatalogDiscoveryReport:
        """Parcourt tous les sitemaps produits, déduplique par SKU.

        ``limit`` borne le nombre de SKU uniques (tests / paliers).
        Ordre = première apparition dans l'ordre des fichiers puis locs.
        """
        report = CatalogDiscoveryReport()
        if limit is not None and limit < 0:
            raise ValueError("limit doit être >= 0")
        if limit == 0:
            return report

        seen: set[str] = set()
        try:
            sitemap_urls = self.discoverer.list_product_sitemap_urls()
        except BricoDepotSitemapError as exc:
            report.errors.append(f"sitemap: {exc}")
            return report

        report.product_sitemap_files = len(sitemap_urls)
        for sm_url in sitemap_urls:
            try:
                status, body = self.discoverer.http_get(sm_url)
            except BricoDepotSitemapError as exc:
                report.errors.append(f"sitemap: {exc}")
                continue
            if status != 200:
                report.errors.append(f"sitemap HTTP {status}: {sm_url}")
                continue
            if not is_product_sitemap_url(sm_url):
                continue
            text = body.decode("utf-8", errors="replace")
            for loc in extract_locs(text):
                report.sitemap_url_count += 1
                discovered = parse_product_url(loc)
                if discovered is None:
                    report.invalid_skus += 1
                    continue
                sku = discovered.supplier_reference
                if not SKU_RE.match(sku):
                    report.invalid_skus += 1
                    continue
                if sku in seen:
                    report.duplicate_skus += 1
                    continue
                seen.add(sku)
                report.products.append(discovered)
                if limit is not None and len(report.products) >= limit:
                    report.discovered_total = len(report.products)
                    return report

        report.discovered_total = len(report.products)
        return report
