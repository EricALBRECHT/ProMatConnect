"""Client lecture seule du catalogue public Gedimat (Algolia).

Les paramètres d'appel (application id, clé de recherche, validUntil) sont
lus dans le HTML public, comme le fait le navigateur. La clé n'est pas
stockée dans le code : elle change à chaque chargement de page.

Ce module ne crée aucun Product, SupplierProduct, ni écriture PostgreSQL.
La fonction `cache_identity` prépare seulement la clé du futur cache live.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

PUBLIC_PAGE_URL = "https://www.gedimat.fr/"
PRICE_CARD_URL = "https://www.gedimat.fr/produits/prix"
USER_AGENT = "ProMatConnect-GedimatPublic/0.1 (+catalog-read)"
PAGINATION_LIMIT = 1000
ID_RANGE_LO = 0
ID_RANGE_HI = 2_000_000

CATALOG_ATTRIBUTES = (
    "id",
    "objectID",
    "ean_tellus",
    "tellus",
    "tellus_variant",
    "reference",
    "code_article",
    "code_interne_erp",
    "name",
    "name_variant",
    "legend",
    "list_categories",
    "hierarchical_categories",
    "id_famille",
    "famille_id",
    "properties",
    "brand",
    "unite_vente",
    "conditionnement",
    "thumb",
    "img",
    "prix",
    "prices",
    "tva",
    "store_id",
    "is_available",
    "availability",
    "stock",
    "dispo",
    "quantite_stock",
    "quantite_stock_plateforme",
    "vendable",
    "updated_at",
    "url",
)

# Du plus large au plus fin. brand.name sert seulement si une famille dépasse
# encore la limite de pagination Algolia.
FACET_LEVELS = (
    "hierarchical_categories.lvl0",
    "hierarchical_categories.lvl1",
    "hierarchical_categories.lvl2",
    "brand.name",
)

_ALGOLIA_CALL = re.compile(
    r"algoliasearch\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)",
)
_INDEX_NAME = re.compile(r"algoliaCatalogIndex\s*=\s*'([^']+)'")


@dataclass(frozen=True)
class GedimatPublicConfig:
    application_id: str
    api_key: str
    index_name: str
    valid_until: int | None


def cache_identity(supplier_sku: str, store_id: str | int) -> tuple[str, str, str]:
    """Clé future du cache live : fournisseur + référence + dépôt.

    Pas de persistance ici. Une réponse négative devra être mémorisée
    au même titre qu'une réponse positive, comme pour Brico.
    """
    sku = str(supplier_sku or "").strip()
    if not sku:
        raise ValueError("supplier_sku requis pour la clé de cache Gedimat.")
    return ("GEDIMAT", sku, str(store_id))


def parse_public_config(html: str) -> GedimatPublicConfig:
    """Extrait application id et clé de recherche du HTML public."""
    match = _ALGOLIA_CALL.search(html)
    if match is None:
        raise ValueError("Paramètres Algolia absents de la page publique Gedimat.")
    application_id, api_key = match.group(1), match.group(2)
    index = _INDEX_NAME.search(html)
    return GedimatPublicConfig(
        application_id=application_id,
        api_key=api_key,
        index_name=index.group(1) if index else "Catalog",
        valid_until=_valid_until(api_key),
    )


def _valid_until(api_key: str) -> int | None:
    try:
        raw = base64.b64decode(api_key).decode()
    except Exception:
        return None
    marker = "validUntil="
    if marker not in raw:
        return None
    tail = raw.split(marker, 1)[1]
    digits = []
    for char in tail:
        if char.isdigit():
            digits.append(char)
        else:
            break
    return int("".join(digits)) if digits else None


class GedimatPublicClient:
    """Recherches Algolia du catalogue public d'un magasin."""

    def __init__(
        self,
        config: GedimatPublicConfig,
        *,
        opener: urllib.request.OpenerDirector | None = None,
        timeout_s: float = 40,
    ):
        self.config = config
        self.timeout_s = timeout_s
        self.opener = opener or urllib.request.build_opener()
        self.http_calls = 0

    @classmethod
    def from_public_page(
        cls,
        *,
        page_url: str = PUBLIC_PAGE_URL,
        timeout_s: float = 40,
    ) -> GedimatPublicClient:
        opener = urllib.request.build_opener()
        request = urllib.request.Request(page_url, headers={"User-Agent": USER_AGENT})
        with opener.open(request, timeout=timeout_s) as response:
            html = response.read().decode("iso-8859-15", errors="replace")
        return cls(parse_public_config(html), opener=opener, timeout_s=timeout_s)

    def search(
        self,
        *,
        store_id: int,
        query: str = "",
        extra_filters: str = "",
        page: int = 0,
        hits_per_page: int = 20,
        attributes: tuple[str, ...] | None = CATALOG_ATTRIBUTES,
        facets: tuple[str, ...] = (),
        max_values_per_facet: int = 200,
    ) -> dict:
        filters = f"is_available = 1 AND store_id = {int(store_id)}"
        if extra_filters:
            filters = f"{filters} AND {extra_filters}"
        params: dict[str, str] = {
            "query": query,
            "filters": filters,
            "page": str(page),
            "hitsPerPage": str(hits_per_page),
            "attributesToHighlight": "[]",
        }
        if attributes is not None:
            params["attributesToRetrieve"] = json.dumps(list(attributes))
        if facets:
            params["facets"] = json.dumps(list(facets))
            params["maxValuesPerFacet"] = str(max_values_per_facet)
        body = json.dumps(
            {
                "requests": [
                    {
                        "indexName": self.config.index_name,
                        "params": urllib.parse.urlencode(params),
                    }
                ]
            }
        ).encode()
        endpoint = (
            f"https://{self.config.application_id}-dsn.algolia.net/1/indexes/*/queries"
        )
        payload = None
        status = 0
        last_error: Exception | None = None
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "X-Algolia-Application-Id": self.config.application_id,
            "X-Algolia-API-Key": self.config.api_key,
        }
        for _attempt in range(3):
            request = urllib.request.Request(endpoint, data=body, headers=headers)
            try:
                with self.opener.open(request, timeout=self.timeout_s) as response:
                    status = response.status
                    payload = json.loads(response.read().decode())
                self.http_calls += 1
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"Algolia HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
        if payload is None:
            raise RuntimeError(f"Algolia injoignable: {last_error}") from last_error
        results = payload.get("results") or []
        if not results:
            raise RuntimeError(f"Réponse Algolia vide (HTTP {status}).")
        result = results[0]
        result["_http_status"] = status
        return result

    def lookup_skus(self, store_id: int, skus: list[str]) -> dict[str, dict]:
        """Retrouve des hits par tellus_variant (requête texte, pas un filtre).

        tellus_variant n'est pas une facette filtrable. La requête égale au code
        renvoie le groupe distinct correspondant.
        """
        found: dict[str, dict] = {}
        for raw in skus:
            sku = str(raw).strip()
            if not sku or sku in found:
                continue
            result = self.search(
                store_id=store_id,
                query=sku,
                hits_per_page=5,
            )
            for hit in result.get("hits") or []:
                if str(hit.get("tellus_variant") or "") == sku:
                    found[sku] = hit
                    break
        return found

    def facet_counts(
        self,
        *,
        store_id: int,
        facet: str,
        extra_filters: str = "",
    ) -> tuple[int, dict[str, int]]:
        result = self.search(
            store_id=store_id,
            extra_filters=extra_filters,
            hits_per_page=0,
            attributes=None,
            facets=(facet,),
        )
        counts = ((result.get("facets") or {}).get(facet) or {})
        return int(result.get("nbHits") or 0), {str(k): int(v) for k, v in counts.items()}

    def iter_segment_hits(self, *, store_id: int, extra_filters: str = ""):
        page = 0
        seen = 0
        total = None
        while True:
            result = self.search(
                store_id=store_id,
                extra_filters=extra_filters,
                page=page,
                hits_per_page=PAGINATION_LIMIT,
            )
            message = result.get("message") or ""
            if message:
                if page > 0:
                    return
                raise RuntimeError(message)
            hits = result.get("hits") or []
            total = int(result.get("nbHits") or 0)
            for hit in hits:
                yield hit
            seen += len(hits)
            if not hits or seen >= total or len(hits) < PAGINATION_LIMIT:
                return
            page += 1

    def collect_store(
        self,
        store_id: int,
    ) -> dict:
        """Parcourt le magasin par facettes, puis complète par tranches d'id.

        Les facettes hiérarchiques se recouvrent et leurs comptes ne sont pas
        exhaustifs. Une partition `id` (chaque tranche <= 1000 hits, compte
        exhaustif) rattrape les références absentes de l'arbre. Déduplication
        sur objectID, sinon id. Aucune écriture base.
        """
        leaves, facet_values = self._leaves(store_id)
        products: dict[str, dict] = {}
        fetched = 0
        for extra in leaves:
            for hit in self.iter_segment_hits(store_id=store_id, extra_filters=extra):
                fetched += 1
                _remember(products, hit)
        facet_unique = len(products)
        id_ranges = list(self._id_ranges(store_id))
        id_fetched = 0
        for extra in id_ranges:
            for hit in self.iter_segment_hits(store_id=store_id, extra_filters=extra):
                id_fetched += 1
                fetched += 1
                _remember(products, hit)
        references = {
            str(hit.get("tellus_variant"))
            for hit in products.values()
            if hit.get("tellus_variant") not in (None, "")
        }
        return {
            "store_id": int(store_id),
            "segments": len(leaves),
            "id_ranges": len(id_ranges),
            "facet_values": facet_values,
            "facet_unique": facet_unique,
            "id_partition_fetched": id_fetched,
            "fetched": fetched,
            "unique": len(products),
            "unique_tellus_variant": len(references),
            "duplicates": fetched - len(products),
            "products": products,
        }

    def _id_ranges(self, store_id: int, extra: str = ""):
        """Tranches d'id disjointes dont le nbHits est exhaustif et <= 1000."""

        def split(lo: int, hi: int):
            clause = _and(extra, f"id >= {lo} AND id <= {hi}")
            result = self.search(
                store_id=store_id,
                extra_filters=clause,
                hits_per_page=0,
                attributes=None,
            )
            count = int(result.get("nbHits") or 0)
            if count == 0:
                return
            exhaustive = result.get("exhaustiveNbHits") is True
            if exhaustive and count <= PAGINATION_LIMIT:
                yield clause
                return
            if lo >= hi:
                yield clause
                return
            mid = (lo + hi) // 2
            yield from split(lo, mid)
            yield from split(mid + 1, hi)

        yield from split(ID_RANGE_LO, ID_RANGE_HI)

    def _leaves(self, store_id: int) -> tuple[list[str], dict[str, int]]:
        leaves: list[str] = []
        seen: dict[str, int] = {level: 0 for level in FACET_LEVELS}

        def walk(extra: str, level: int) -> None:
            facet = FACET_LEVELS[level] if level < len(FACET_LEVELS) else None
            if facet is None:
                leaves.append(extra)
                return
            total, counts = self.facet_counts(
                store_id=store_id, facet=facet, extra_filters=extra
            )
            if total <= PAGINATION_LIMIT or not counts:
                leaves.append(extra)
                return
            seen[facet] += len(counts)
            for name, count in counts.items():
                child = _and(extra, f"{facet}:{_quote(name)}")
                if count <= PAGINATION_LIMIT:
                    leaves.append(child)
                else:
                    walk(child, level + 1)

        walk("", 0)
        return leaves, seen


def _remember(products: dict[str, dict], hit: dict) -> None:
    key = str(hit.get("objectID") or hit.get("id") or "")
    if key:
        products.setdefault(key, hit)


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _and(left: str, right: str) -> str:
    return right if not left else f"{left} AND {right}"


def fetch_price_cards(product_ids: list[int], *, timeout_s: float = 40) -> tuple[int, dict]:
    """POST public /produits/prix. Retourne (status, corps JSON ou texte)."""
    body = json.dumps({"productIds": [int(pid) for pid in product_ids]}).encode()
    request = urllib.request.Request(
        PRICE_CARD_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PUBLIC_PAGE_URL,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw[:400]}
        return exc.code, parsed
