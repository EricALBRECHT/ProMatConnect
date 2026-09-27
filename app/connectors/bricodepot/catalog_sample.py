"""Échantillon catalogue diversifié — familles Magento + round-robin déterministe.

Stratégie (coût réseau bas) :
  1. Lister les grandes familles via categoryList(Produits) + cuisine.
  2. Pour chaque famille, paginer products(filter: category_uid) — SKU seuls.
  3. Round-robin déterministe entre familles jusqu'à ``limit`` SKU uniques.
  4. Enrichissement GraphQL catalogue uniquement pour les SKU sélectionnés.

Aucun random(). Aucun enrichissement des ~27k SKU pour connaître les catégories.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field
from typing import Any

from app.connectors.bricodepot.catalog_dto import BricoDepotDiscoveredProduct
from app.connectors.bricodepot.client import (
    DEFAULT_STORE_ID,
    BricoDepotClient,
    _as_int,
    _as_str,
    _unwrap_payload,
    build_product_headers,
)
from app.connectors.bricodepot.queries import load_query

# Racine merchandising « Produits » (validée côté Magento public).
PRODUITS_CATEGORY_ID = "8089"
# Cuisine n'apparaît pas dans les children de 8089 — lookup dédié.
EXTRA_FAMILY_URL_PATHS = ("produits/cuisine",)

# pageSize demandé ; Magento plafonne souvent à 36.
CATEGORY_PRODUCT_PAGE_SIZE = 36

# Segments promo / opérations à exclure si jamais présents dans categoryList.
_PROMO_FAMILY_PREFIXES = (
    "op-",
    "opbdfr",
    "opebdfr",
    "nouveautes",
    "premiers-prix",
    "top-",
    "destockage",
    "baisse-de-prix",
    "offres-",
    "anti-inflation",
    "tous-les-produits",
    "book-",
    "fete-",
    "nos-offres",
    "officiel-",
)


def family_slug_from_url_path(url_path: str | None) -> str | None:
    """Premier segment utile après produits/ (taxonomie source Brico)."""
    if not url_path:
        return None
    parts = [p for p in str(url_path).strip("/").split("/") if p]
    if not parts:
        return None
    if parts[0] == "produits":
        return parts[1] if len(parts) >= 2 else None
    return parts[0]


def magento_category_id_from_uid(uid: str) -> str | None:
    """Id Magento (string) encodé dans l'uid GraphQL (base64)."""
    if not uid:
        return None
    try:
        return base64.b64decode(uid).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return None


def is_promo_family_slug(slug: str | None) -> bool:
    if not slug:
        return True
    low = slug.lower()
    return any(low.startswith(p) for p in _PROMO_FAMILY_PREFIXES)


def round_robin_sample(
    family_order: list[str],
    pools: dict[str, list[str]],
    *,
    limit: int,
) -> list[tuple[str, str]]:
    """Sélection déterministe : 1 SKU/famille à chaque tour, sans doublon.

    ``family_order`` fixe l'ordre des tours. À l'intérieur d'une famille,
    l'ordre de ``pools[family]`` est conservé (ordre Magento/page).
    Les familles épuisées sont sautées ; le tour continue jusqu'à ``limit``
    ou jusqu'à épuisement global.
    """
    if limit < 1:
        raise ValueError("limit doit être >= 1")
    if not family_order:
        return []

    indices = {fam: 0 for fam in family_order}
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    while len(out) < limit:
        progressed = False
        for fam in family_order:
            if len(out) >= limit:
                break
            pool = pools.get(fam) or []
            idx = indices[fam]
            while idx < len(pool) and pool[idx] in seen:
                idx += 1
            indices[fam] = idx
            if idx >= len(pool):
                continue
            sku = pool[idx]
            indices[fam] = idx + 1
            seen.add(sku)
            out.append((sku, fam))
            progressed = True
        if not progressed:
            break
    return out


def concentration_stats(family_counts: dict[str, int]) -> dict[str, Any]:
    """top1/top2 % + diversity_warning (top2 > 70)."""
    total = sum(family_counts.values())
    if total <= 0:
        return {
            "families_represented": 0,
            "distribution": [],
            "top1_category_pct": 0.0,
            "top2_category_pct": 0.0,
            "diversity_warning": False,
        }
    ranked = sorted(family_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    distribution = [
        {
            "category": cat,
            "count": n,
            "pct": round(100.0 * n / total, 1),
        }
        for cat, n in ranked
    ]
    top1 = distribution[0]["pct"] if distribution else 0.0
    top2 = sum(c["pct"] for c in distribution[:2]) if distribution else 0.0
    return {
        "families_represented": len(distribution),
        "distribution": distribution,
        "top1_category_pct": top1,
        "top2_category_pct": top2,
        # Critère explicite : top2 > 70 % (pas >=).
        "diversity_warning": top2 > 70.0,
    }


@dataclass(frozen=True)
class BricoDepotFamily:
    uid: str
    name: str
    url_path: str
    family_slug: str
    product_count: int


@dataclass
class DiverseSampleResult:
    families: list[BricoDepotFamily] = field(default_factory=list)
    selected: list[BricoDepotDiscoveredProduct] = field(default_factory=list)
    family_by_sku: dict[str, str] = field(default_factory=dict)
    family_inventory: list[dict[str, Any]] = field(default_factory=list)
    selection_counts: dict[str, int] = field(default_factory=dict)
    graphql_calls: int = 0
    errors: list[str] = field(default_factory=list)

    def concentration(self) -> dict[str, Any]:
        return concentration_stats(self.selection_counts)


class BricoDepotDiverseSampler:
    """Découverte familles + échantillon round-robin via category_uid."""

    def __init__(
        self,
        client: BricoDepotClient,
        *,
        seller_id: str | int = "10",
        store_id: str = DEFAULT_STORE_ID,
        page_size: int = CATEGORY_PRODUCT_PAGE_SIZE,
        produits_category_id: str = PRODUITS_CATEGORY_ID,
        extra_family_url_paths: tuple[str, ...] = EXTRA_FAMILY_URL_PATHS,
    ):
        self.client = client
        self.seller_id = str(seller_id)
        self.store_id = str(store_id)
        self.page_size = max(1, int(page_size))
        self.produits_category_id = str(produits_category_id)
        self.extra_family_url_paths = tuple(extra_family_url_paths)
        self._families_query: str | None = None
        self._by_path_query: str | None = None
        self._children_query: str | None = None
        self._products_query: str | None = None
        self.graphql_calls = 0

    def _headers(self) -> dict[str, str]:
        return build_product_headers(seller_id=self.seller_id, store_id=self.store_id)

    def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = self.client._post(
            query=query, query_variables=variables, headers=self._headers()
        )
        self.graphql_calls += 1
        return _unwrap_payload(body)

    @property
    def families_query(self) -> str:
        if self._families_query is None:
            self._families_query = load_query("category_families.graphql")
        return self._families_query

    @property
    def by_path_query(self) -> str:
        if self._by_path_query is None:
            self._by_path_query = load_query("category_by_url_path.graphql")
        return self._by_path_query

    @property
    def products_query(self) -> str:
        if self._products_query is None:
            self._products_query = load_query("products_by_category.graphql")
        return self._products_query

    @property
    def children_query(self) -> str:
        if self._children_query is None:
            self._children_query = load_query("category_children.graphql")
        return self._children_query

    @staticmethod
    def _parse_family_node(node: dict[str, Any]) -> BricoDepotFamily | None:
        uid = _as_str(node.get("uid"))
        url_path = _as_str(node.get("url_path")) or ""
        slug = family_slug_from_url_path(url_path)
        if not uid or not slug or is_promo_family_slug(slug):
            return None
        return BricoDepotFamily(
            uid=uid,
            name=_as_str(node.get("name")) or slug,
            url_path=url_path,
            family_slug=slug,
            product_count=int(_as_int(node.get("product_count")) or 0),
        )

    def list_families(self) -> list[BricoDepotFamily]:
        """Familles merchandising Brico, triées par url_path (déterministe)."""
        by_slug: dict[str, BricoDepotFamily] = {}

        root = self._gql(
            self.families_query, {"id": self.produits_category_id}
        )
        data = root.get("data", root)
        cat_list = data.get("categoryList") if isinstance(data, dict) else None
        roots = cat_list if isinstance(cat_list, list) else []
        for root_cat in roots:
            if not isinstance(root_cat, dict):
                continue
            children = root_cat.get("children") or []
            if not isinstance(children, list):
                continue
            for child in children:
                if not isinstance(child, dict):
                    continue
                fam = self._parse_family_node(child)
                if fam is not None:
                    by_slug[fam.family_slug] = fam

        for url_path in self.extra_family_url_paths:
            payload = self._gql(self.by_path_query, {"urlPath": url_path})
            data = payload.get("data", payload)
            cats = data.get("categories") if isinstance(data, dict) else None
            items = (cats or {}).get("items") if isinstance(cats, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                fam = self._parse_family_node(item)
                if fam is not None:
                    by_slug.setdefault(fam.family_slug, fam)

        return sorted(by_slug.values(), key=lambda f: f.url_path)

    def _child_category_uids(self, family: BricoDepotFamily) -> list[str]:
        """Uids des sous-catégories directes (ordre Magento)."""
        cat_id = magento_category_id_from_uid(family.uid)
        if not cat_id:
            return []
        payload = self._gql(self.children_query, {"id": cat_id})
        data = payload.get("data", payload)
        cat_list = data.get("categoryList") if isinstance(data, dict) else None
        roots = cat_list if isinstance(cat_list, list) else []
        if not roots or not isinstance(roots[0], dict):
            return []
        children = roots[0].get("children") or []
        if not isinstance(children, list):
            return []
        out: list[str] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            uid = _as_str(child.get("uid"))
            if not uid:
                continue
            if int(_as_int(child.get("product_count")) or 0) <= 0:
                continue
            out.append(uid)
        return out

    def _paginate_category_skus(
        self, category_uid: str, *, max_skus: int, seen: set[str] | None = None
    ) -> list[str]:
        """SKU d'une catégorie (uid), pagination Magento, dédupliqués."""
        if max_skus < 1:
            return []
        out: list[str] = []
        local_seen = seen if seen is not None else set()
        page = 1
        total_pages: int | None = None
        while len(out) < max_skus:
            if total_pages is not None and page > total_pages:
                break
            payload = self._gql(
                self.products_query,
                {
                    "filter": {"category_uid": {"eq": category_uid}},
                    "currentPage": page,
                    "pageSize": self.page_size,
                },
            )
            data = payload.get("data", payload)
            products = data.get("products") if isinstance(data, dict) else None
            if not isinstance(products, dict):
                break
            info = products.get("page_info") or {}
            if isinstance(info, dict):
                tp = _as_int(info.get("total_pages"))
                if tp is not None:
                    total_pages = tp
            items = products.get("items") or []
            if not isinstance(items, list) or not items:
                break
            added = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                sku = _as_str(item.get("sku"))
                if not sku or sku in local_seen:
                    continue
                local_seen.add(sku)
                out.append(sku)
                added += 1
                if len(out) >= max_skus:
                    break
            if added == 0:
                break
            page += 1
        return out

    def fetch_family_skus(
        self, family: BricoDepotFamily, *, max_skus: int
    ) -> list[str]:
        """SKU d'une famille, ordre Magento (page 1, 2, …), dédupliqués."""
        if max_skus < 1:
            return []
        seen: set[str] = set()
        out = self._paginate_category_skus(
            family.uid, max_skus=max_skus, seen=seen
        )
        # Certaines familles (ex. plomberie) : product_count sur le parent mais
        # products(category_uid=parent) vide — les SKU sont sur les enfants.
        if out or not family.product_count:
            return out
        for child_uid in self._child_category_uids(family):
            chunk = self._paginate_category_skus(
                child_uid, max_skus=max_skus - len(out), seen=seen
            )
            out.extend(chunk)
            if len(out) >= max_skus:
                break
        return out

    def sample_round_robin(self, *, limit: int) -> DiverseSampleResult:
        """Construit jusqu'à ``limit`` SKU via round-robin multi-familles."""
        self.graphql_calls = 0
        result = DiverseSampleResult()
        try:
            families = self.list_families()
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"familles: {exc}")
            result.graphql_calls = self.graphql_calls
            return result

        result.families = families
        result.family_inventory = [
            {
                "family": f.family_slug,
                "uid": f.uid,
                "url_path": f.url_path,
                "name": f.name,
                "available": f.product_count,
            }
            for f in families
        ]
        if not families:
            result.errors.append("aucune famille merchandising trouvée")
            result.graphql_calls = self.graphql_calls
            return result

        # Assez de SKU par famille pour un tour complet + marge anti-doublons.
        per_family = max(1, math.ceil(limit / len(families))) + 8
        pools: dict[str, list[str]] = {}
        for fam in families:
            try:
                want = min(per_family, fam.product_count) if fam.product_count else per_family
                want = max(want, per_family)
                pools[fam.family_slug] = self.fetch_family_skus(fam, max_skus=want)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{fam.family_slug}: {exc}")
                pools[fam.family_slug] = []

        order = [f.family_slug for f in families]
        picked = round_robin_sample(order, pools, limit=limit)

        # Si doublons inter-familles ont réduit le pool, compléter en refetch.
        if len(picked) < limit:
            for fam in families:
                if len(picked) >= limit:
                    break
                have = len(pools.get(fam.family_slug) or [])
                if fam.product_count and have >= fam.product_count:
                    continue
                try:
                    extra = self.fetch_family_skus(
                        fam, max_skus=max(have + self.page_size, per_family * 2)
                    )
                    pools[fam.family_slug] = extra
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"{fam.family_slug}/refill: {exc}")
            picked = round_robin_sample(order, pools, limit=limit)

        counts: dict[str, int] = {}
        family_by_sku: dict[str, str] = {}
        discovered: list[BricoDepotDiscoveredProduct] = []
        for sku, fam in picked:
            family_by_sku[sku] = fam
            counts[fam] = counts.get(fam, 0) + 1
            discovered.append(
                BricoDepotDiscoveredProduct(
                    supplier_reference=sku,
                    product_url=f"https://www.bricodepot.fr/p/{sku}",
                    slug=None,
                )
            )

        result.selected = discovered
        result.family_by_sku = family_by_sku
        result.selection_counts = dict(sorted(counts.items(), key=lambda kv: kv[0]))
        result.graphql_calls = self.graphql_calls
        return result
