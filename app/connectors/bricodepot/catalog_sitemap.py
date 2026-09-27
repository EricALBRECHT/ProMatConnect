"""Découverte catalogue Brico Dépôt via sitemaps publics.

Aucun cookie/session. Transport HTTP injectable pour tests offline.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from app.connectors.bricodepot.catalog_dto import BricoDepotDiscoveredProduct

SITEMAP_INDEX_URL = "https://www.bricodepot.fr/sitemaps/sitemap.xml"
PRODUCT_SITEMAP_RE = re.compile(r"sitemap-produits-\d+\.xml", re.I)
SKU_PATH_RE = re.compile(r"^/p/(\d{8,14})(?:/([^/?#]*))?/?$")
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
UA = "ProMatConnect-BricoDepotCatalog/0.1 (+sitemap; no-session)"

HttpGet = Callable[[str], tuple[int, bytes]]


class BricoDepotSitemapError(Exception):
    """Erreur lecture / parsing sitemap."""


def default_http_get(url: str, *, timeout: float = 40.0) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/xml,text/xml,*/*"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)), resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return int(exc.code), body
    except urllib.error.URLError as exc:
        raise BricoDepotSitemapError(f"réseau: {exc.reason}") from exc


def extract_locs(xml_text: str) -> list[str]:
    return [m.group(1).strip() for m in LOC_RE.finditer(xml_text)]


def is_product_sitemap_url(url: str) -> bool:
    path = urlparse(url).path or ""
    return bool(PRODUCT_SITEMAP_RE.search(path.rsplit("/", 1)[-1]))


def parse_product_url(url: str) -> BricoDepotDiscoveredProduct | None:
    """Extrait SKU + slug depuis une URL /p/{SKU}/… — None si invalide."""
    text = (url or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if host and host not in {"www.bricodepot.fr", "bricodepot.fr"}:
        return None
    match = SKU_PATH_RE.match(parsed.path or "")
    if not match:
        return None
    sku = match.group(1)
    slug = match.group(2) or None
    if slug == "":
        slug = None
    # Canonicaliser sans fragment/query
    path = f"/p/{sku}" + (f"/{slug}" if slug else "")
    product_url = f"https://www.bricodepot.fr{path}"
    return BricoDepotDiscoveredProduct(
        supplier_reference=sku,
        product_url=product_url,
        slug=slug,
    )


@dataclass
class BricoDepotSitemapDiscoverer:
    """Lit l'index sitemap puis les sitemaps produits uniquement."""

    index_url: str = SITEMAP_INDEX_URL
    http_get: HttpGet = default_http_get

    def list_product_sitemap_urls(self) -> list[str]:
        status, body = self.http_get(self.index_url)
        if status != 200:
            raise BricoDepotSitemapError(f"index HTTP {status}")
        text = body.decode("utf-8", errors="replace")
        locs = extract_locs(text)
        product = [u for u in locs if is_product_sitemap_url(u)]
        if not product:
            raise BricoDepotSitemapError("aucun sitemap-produits-* dans l'index")
        return product

    def iter_product_urls(self, *, limit: int | None = None) -> list[BricoDepotDiscoveredProduct]:
        """Découvre les produits, dédupliqués par SKU, ordre de première apparition.

        ``limit`` borne le nombre de SKU uniques retournés (None = pas de borne).
        """
        if limit is not None and limit < 0:
            raise ValueError("limit doit être >= 0")
        if limit == 0:
            return []

        seen: set[str] = set()
        out: list[BricoDepotDiscoveredProduct] = []
        for sm_url in self.list_product_sitemap_urls():
            status, body = self.http_get(sm_url)
            if status != 200:
                raise BricoDepotSitemapError(f"sitemap produits HTTP {status}: {sm_url}")
            text = body.decode("utf-8", errors="replace")
            for loc in extract_locs(text):
                discovered = parse_product_url(loc)
                if discovered is None:
                    continue
                if discovered.supplier_reference in seen:
                    continue
                seen.add(discovered.supplier_reference)
                out.append(discovered)
                if limit is not None and len(out) >= limit:
                    return out
        return out

    def discover_products(self, *, limit: int | None = None) -> list[BricoDepotDiscoveredProduct]:
        return self.iter_product_urls(limit=limit)
