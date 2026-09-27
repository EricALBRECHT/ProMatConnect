#!/usr/bin/env python3
"""Audit public — découverte catalogue Brico Dépôt (sans Sensefuel).

Lecture seule. Aucune DB. Aucun cookie/session navigateur.
Ne modifie pas le connecteur live.

Couvre :
  - robots.txt / sitemap index (structure)
  - échantillon sitemap produits → SKU depuis /p/{sku}/…
  - JSON-LD + champs utiles page produit
  - Magento customQuery : sku / category_uid / search + pagination

Usage :
  python tools/bricodepot_catalog_discovery_audit.py
  python tools/bricodepot_catalog_discovery_audit.py --feasibility 15
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

UA = "ProMatConnect-BricoCatalogDiscoveryAudit/0.1 (+public-audit; controlled)"
BASE = "https://www.bricodepot.fr"
ENDPOINT = f"{BASE}/api/magento/customQuery"
SITEMAP_INDEX = f"{BASE}/sitemaps/sitemap.xml"
# Identifiant magasin public déjà validé (Amiens) — contexte offre, pas auth.
DEFAULT_SELLER_ID = "10"
DEFAULT_STORE_ID = "1"

SKU_IN_PATH = re.compile(r"/p/(\d{8,14})(?:/|$)")


def http_get(url: str, *, timeout: float = 40.0) -> tuple[int, bytes, str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "*/*"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        return int(getattr(resp, "status", 200)), body, ct


def http_post_json(
    url: str, payload: Any, headers: dict[str, str], *, timeout: float = 40.0
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return int(getattr(resp, "status", 200)), json.loads(raw.decode("utf-8"))


def product_headers(seller_id: str = DEFAULT_SELLER_ID, store_id: str = DEFAULT_STORE_ID) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": UA,
        "X-BricoDepot-Context": (
            f"customerGroupId=0;customerSegmentIds=1;isLoggedIn=0;"
            f"sellerId={seller_id};storeId={store_id}"
        ),
    }


def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    status, body = http_post_json(
        ENDPOINT,
        [{"query": query, "queryVariables": variables or {}}],
        product_headers(),
    )
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    payload = body[0] if isinstance(body, list) else body
    if not isinstance(payload, dict):
        raise RuntimeError("Réponse GraphQL invalide")
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors: {payload['errors']!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("data manquant")
    return data


def audit_robots_and_sitemaps() -> dict[str, Any]:
    status, body, _ = http_get(f"{BASE}/robots.txt")
    robots = body.decode("utf-8", errors="replace")
    sitemap_lines = [
        line.split(":", 1)[1].strip()
        for line in robots.splitlines()
        if line.lower().startswith("sitemap:")
    ]

    # Index : robots pointe vers /sitemaps/sitemap.xml (équivalent aussi /sitemap.xml)
    idx_url = sitemap_lines[0] if sitemap_lines else SITEMAP_INDEX
    st, raw, ct = http_get(idx_url)
    text = raw.decode("utf-8", errors="replace")
    locs = re.findall(r"<loc>\s*([^<]+)\s*</loc>", text, flags=re.I)

    # Un seul fichier produits pour structure (pas de dump massif)
    product_maps = [u for u in locs if "sitemap-produits-" in u]
    sample_url = product_maps[0] if product_maps else None
    sample: dict[str, Any] = {}
    if sample_url:
        pst, praw, _ = http_get(sample_url)
        ptext = praw.decode("utf-8", errors="replace")
        plocs = re.findall(r"<loc>\s*([^<]+)\s*</loc>", ptext, flags=re.I)
        skus = []
        for loc in plocs[:20]:
            m = SKU_IN_PATH.search(urlparse(loc).path)
            if m:
                skus.append({"sku": m.group(1), "url": loc})
        sample = {
            "url": sample_url,
            "status": pst,
            "bytes": len(praw),
            "loc_count": len(plocs),
            "p_sku_pattern_count": sum(
                1 for u in plocs if SKU_IN_PATH.search(urlparse(u).path)
            ),
            "url_form": "/p/{sku}/{slug}",
            "first_skus": skus[:8],
        }

    # Comptage contrôlé : 1 GET par sitemap-produits-N (≤6), pas de dump disque.
    product_counts = []
    for u in product_maps:
        pst, praw, _ = http_get(u)
        ptext = praw.decode("utf-8", errors="replace")
        plocs = re.findall(r"<loc>\s*([^<]+)\s*</loc>", ptext, flags=re.I)
        product_counts.append(
            {
                "file": u.rsplit("/", 1)[-1],
                "status": pst,
                "locs": len(plocs),
                "sample": plocs[0] if plocs else None,
            }
        )

    return {
        "robots_status": status,
        "sitemap_declared": sitemap_lines,
        "index_status": st,
        "index_content_type": ct,
        "index_children": locs,
        "product_sitemap_sample": sample,
        "product_sitemap_counts": product_counts,
        "approx_product_urls": sum(c["locs"] for c in product_counts),
    }


def audit_product_page(sku: str) -> dict[str, Any]:
    status, body, _ = http_get(f"{BASE}/p/{sku}")
    html = body.decode("utf-8", errors="replace")
    ld_blocks = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    ):
        try:
            ld_blocks.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue

    flat: list[dict[str, Any]] = []
    for b in ld_blocks:
        if isinstance(b, list):
            flat.extend(x for x in b if isinstance(x, dict))
        elif isinstance(b, dict) and "@graph" in b and isinstance(b["@graph"], list):
            flat.extend(x for x in b["@graph"] if isinstance(x, dict))
        elif isinstance(b, dict):
            flat.append(b)

    products = [
        p
        for p in flat
        if p.get("@type") == "Product"
        or (isinstance(p.get("@type"), list) and "Product" in p["@type"])
    ]
    crumbs = [
        p
        for p in flat
        if p.get("@type") == "BreadcrumbList"
        or (isinstance(p.get("@type"), list) and "BreadcrumbList" in p["@type"])
    ]

    prod = products[0] if products else {}
    brand = prod.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")

    return {
        "sku_requested": sku,
        "http_status": status,
        "bytes": len(body),
        "json_ld_product": {
            "name": prod.get("name"),
            "sku": prod.get("sku"),
            "gtin13": prod.get("gtin13") or prod.get("gtin"),
            "brand": brand,
            "category": prod.get("category"),
            "image": prod.get("image") if isinstance(prod.get("image"), str) else (
                (prod.get("image") or [None])[0] if isinstance(prod.get("image"), list) else None
            ),
            "description_len": len(str(prod.get("description") or "")),
            "offers_price": (prod.get("offers") or {}).get("price")
            if isinstance(prod.get("offers"), dict)
            else None,
        },
        "breadcrumb_names": [
            (it.get("name") or (it.get("item") or {}).get("name") if isinstance(it.get("item"), dict) else None)
            for b in crumbs
            for it in (b.get("itemListElement") or [])
            if isinstance(it, dict)
        ],
        "has_nuxt_data": "__NUXT_DATA__" in html,
        "html_field_mentions": {
            k: html.lower().count(k)
            for k in (
                "conditionnement",
                "content_net",
                "ean_code",
                "sap_easier",
                "sku",
                "gtin",
            )
        },
    }


def audit_graphql_capabilities() -> dict[str, Any]:
    out: dict[str, Any] = {}

    # SKU known
    data = gql(
        """
        query($filter: ProductAttributeFilterInput, $currentPage: Int, $pageSize: Int) {
          products(filter: $filter, currentPage: $currentPage, pageSize: $pageSize) {
            total_count
            page_info { current_page page_size total_pages }
            items {
              id uid name sku ean_code sap_easier_code
              conditionnement_label content_net_value content_net_unit_label
              images { role url }
              categories { name uid url_path }
            }
          }
        }
        """,
        {"filter": {"sku": {"in": ["3334160524579"]}}, "currentPage": 1, "pageSize": 10},
    )
    item = (data.get("products") or {}).get("items") or []
    out["by_sku"] = {
        "total_count": (data.get("products") or {}).get("total_count"),
        "item": item[0] if item else None,
    }

    # Category by url_path
    cat = gql(
        """
        query($urlPath: String!) {
          categories(filters: { url_path: { eq: $urlPath } }, pageSize: 5, currentPage: 1) {
            total_count
            items { uid id name url_path product_count }
          }
        }
        """,
        {
            "urlPath": "produits/materiau-et-gros-oeuvre/isolation-et-cloison/plaque-de-platre"
        },
    )
    cat_item = ((cat.get("categories") or {}).get("items") or [None])[0]
    out["category_by_url_path"] = cat_item

    # Products by category_uid + page 2
    if cat_item and cat_item.get("uid"):
        uid = cat_item["uid"]
        p1 = gql(
            """
            query($filter: ProductAttributeFilterInput, $currentPage: Int, $pageSize: Int) {
              products(filter: $filter, currentPage: $currentPage, pageSize: $pageSize) {
                total_count
                page_info { current_page page_size total_pages }
                items { sku name ean_code conditionnement_label content_net_unit_label }
              }
            }
            """,
            {"filter": {"category_uid": {"eq": uid}}, "currentPage": 1, "pageSize": 10},
        )
        p2 = gql(
            """
            query($filter: ProductAttributeFilterInput, $currentPage: Int, $pageSize: Int) {
              products(filter: $filter, currentPage: $currentPage, pageSize: $pageSize) {
                total_count
                page_info { current_page page_size total_pages }
                items { sku name }
              }
            }
            """,
            {"filter": {"category_uid": {"eq": uid}}, "currentPage": 2, "pageSize": 10},
        )
        out["by_category_uid"] = {
            "uid": uid,
            "page1_total": (p1.get("products") or {}).get("total_count"),
            "page1_info": (p1.get("products") or {}).get("page_info"),
            "page1_skus": [i.get("sku") for i in ((p1.get("products") or {}).get("items") or [])],
            "page2_info": (p2.get("products") or {}).get("page_info"),
            "page2_skus": [i.get("sku") for i in ((p2.get("products") or {}).get("items") or [])],
        }

    # Full-text search argument
    search = gql(
        """
        query($search: String, $currentPage: Int, $pageSize: Int) {
          products(search: $search, currentPage: $currentPage, pageSize: $pageSize) {
            total_count
            page_info { current_page page_size total_pages }
            items { sku name ean_code }
          }
        }
        """,
        {"search": "ba13", "currentPage": 1, "pageSize": 10},
    )
    out["by_search_arg"] = {
        "total_count": (search.get("products") or {}).get("total_count"),
        "page_info": (search.get("products") or {}).get("page_info"),
        "skus": [i.get("sku") for i in ((search.get("products") or {}).get("items") or [])],
    }

    # Negatives (documented)
    out["negatives"] = {
        "filter.name.match": "invalid — FilterEqualTypeInput (eq/in only)",
        "filter.search": "invalid — not in ProductAttributeFilterInput",
        "use_instead": "products(search: $search) query argument",
    }
    return out


def feasibility_from_graphql(limit: int = 15) -> list[dict[str, Any]]:
    """Petit échantillon contrôlé via category_uid (pas d'import DB)."""
    cat = gql(
        """
        query($urlPath: String!) {
          categories(filters: { url_path: { eq: $urlPath } }, pageSize: 1, currentPage: 1) {
            items { uid name }
          }
        }
        """,
        {
            "urlPath": "produits/materiau-et-gros-oeuvre/isolation-et-cloison/plaque-de-platre"
        },
    )
    uid = ((cat.get("categories") or {}).get("items") or [{}])[0].get("uid")
    if not uid:
        raise RuntimeError("category uid introuvable")

    data = gql(
        """
        query($filter: ProductAttributeFilterInput, $currentPage: Int, $pageSize: Int) {
          products(filter: $filter, currentPage: $currentPage, pageSize: $pageSize) {
            total_count
            items {
              sku name ean_code sap_easier_code
              conditionnement_label content_net_value content_net_unit_label
              images { url }
              categories { name url_path }
            }
          }
        }
        """,
        {
            "filter": {"category_uid": {"eq": uid}},
            "currentPage": 1,
            "pageSize": min(20, max(1, limit)),
        },
    )
    rows = []
    for item in ((data.get("products") or {}).get("items") or [])[:limit]:
        cats = item.get("categories") or []
        leaf = cats[-1] if cats else {}
        imgs = item.get("images") or []
        rows.append(
            {
                "supplier_reference": item.get("sku"),
                "ean_code": item.get("ean_code"),
                "sap_easier_code": item.get("sap_easier_code"),
                "name": item.get("name"),
                "category": leaf.get("name"),
                "category_path": leaf.get("url_path"),
                "brand": None,  # non renvoyé par ce fragment ; présent en JSON-LD
                "image_url": (imgs[0] or {}).get("url") if imgs else None,
                "reference_unit": item.get("conditionnement_label"),
                "content_net_value": item.get("content_net_value"),
                "content_net_unit": item.get("content_net_unit_label"),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feasibility", type=int, default=15, help="Nb produits test (max 20)")
    parser.add_argument("--skip-sitemaps", action="store_true")
    parser.add_argument("--skip-pages", action="store_true")
    parser.add_argument("--skip-graphql", action="store_true")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {"ok": True}

    try:
        if not args.skip_sitemaps:
            print("=== A/B Sitemaps ===")
            report["sitemaps"] = audit_robots_and_sitemaps()
            print(json.dumps(report["sitemaps"], ensure_ascii=False, indent=2))

        if not args.skip_pages:
            print("\\n=== C Pages produits ===")
            pages = []
            for sku in ("3334160524579", "3334160144715", "3596265336819"):
                pages.append(audit_product_page(sku))
            report["product_pages"] = pages
            print(json.dumps(pages, ensure_ascii=False, indent=2))

        if not args.skip_graphql:
            print("\\n=== E Magento GraphQL ===")
            report["graphql"] = audit_graphql_capabilities()
            print(json.dumps(report["graphql"], ensure_ascii=False, indent=2))

            print("\\n=== 6 Faisabilité ===")
            rows = feasibility_from_graphql(min(20, max(1, args.feasibility)))
            report["feasibility"] = rows
            print(json.dumps(rows, ensure_ascii=False, indent=2))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
        report["ok"] = False
        report["error"] = str(exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    print("\\n=== JSON résumé ===")
    print(json.dumps({"ok": report["ok"], "keys": list(report.keys())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
