"""Client HTTP Brico Dépôt — customQuery (produit + store locator).

Aucun cookie/session. Transport injectable pour tests offline.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Protocol

from app.connectors.bricodepot.queries import load_product_query, load_stores_query
from app.services.address_display import retailer_address_fields

ENDPOINT = "https://www.bricodepot.fr/api/magento/customQuery"
DEFAULT_STORE_ID = "1"
DEFAULT_TIMEOUT_S = 30.0
# Observé / validé : pageSize produit = 10 — ne pas dépasser sans revalidation.
PRODUCT_SKU_PAGE_SIZE = 10


class BricoDepotClientError(Exception):
    """Erreur client BD (HTTP, JSON, GraphQL, timeout…)."""


class BricoDepotTimeoutError(BricoDepotClientError):
    pass


class BricoDepotHttpError(BricoDepotClientError):
    def __init__(self, status: int, detail: str = ""):
        self.status = status
        super().__init__(f"HTTP {status}: {detail}")


class BricoDepotGraphQLError(BricoDepotClientError):
    pass


@dataclass(frozen=True)
class BricoRetailer:
    entity_id: int
    seller_code: str | None
    name: str | None
    address: str | None
    city: str | None
    postcode: str | None
    latitude: float | None
    longitude: float | None
    phone: str | None
    distance_km: float | None


@dataclass(frozen=True)
class BricoMoney:
    value: Decimal | None
    currency: str | None = None


@dataclass(frozen=True)
class BricoProductOffer:
    sku: str
    name: str | None
    price_ht_piece: BricoMoney
    price_ttc_piece: BricoMoney
    price_ht_measure: BricoMoney
    price_ttc_measure: BricoMoney
    stock_quantity: int | None
    stock_status: str | None
    is_salable: bool | None
    is_offer_available: bool | None
    raw_item: dict[str, Any]


Transport = Callable[..., tuple[int, Any]]


class SupportsSession(Protocol):
    """Minimal duck-type — évite d'importer Session partout."""


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(value)))
        except Exception:
            return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _descend(root: Any, *path: str) -> Any:
    cur: Any = root
    for key in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _money_from_final_price(node: Any) -> BricoMoney:
    """Lit final_price.value uniquement — jamais regular_price promu."""
    if not isinstance(node, dict):
        return BricoMoney(value=None, currency=None)
    currency = _as_str(node.get("currency"))
    raw = node.get("value")
    if raw is None:
        return BricoMoney(value=None, currency=currency)
    try:
        return BricoMoney(value=Decimal(str(raw)), currency=currency)
    except Exception:
        return BricoMoney(value=None, currency=currency)


def build_context_header(*, seller_id: str, store_id: str = DEFAULT_STORE_ID) -> str:
    return (
        f"customerGroupId=0;customerSegmentIds=1;isLoggedIn=0;"
        f"sellerId={seller_id};storeId={store_id}"
    )


def build_product_headers(*, seller_id: str, store_id: str = DEFAULT_STORE_ID) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ProMatConnect-BricoDepotClient/0.1 (+no-session)",
        "X-BricoDepot-Context": build_context_header(seller_id=seller_id, store_id=store_id),
    }


def build_stores_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ProMatConnect-BricoDepotClient/0.1 (+no-session)",
    }


def normalize_skus(skus: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in skus:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    if not ordered:
        raise ValueError("Au moins un SKU non vide est requis.")
    return ordered


def default_transport(
    *,
    url: str,
    payload: list[dict[str, Any]] | dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read()
    except TimeoutError as exc:
        raise BricoDepotTimeoutError(f"Timeout après {timeout}s") from exc
    except urllib.error.HTTPError as exc:
        detail = (exc.read()[:800] if exc.fp else b"").decode("utf-8", errors="replace")
        raise BricoDepotHttpError(int(exc.code), detail) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise BricoDepotTimeoutError(str(reason)) from exc
        raise BricoDepotClientError(f"Erreur réseau: {reason}") from exc

    if status != 200:
        raise BricoDepotHttpError(status, repr(raw[:800]))
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BricoDepotClientError(f"JSON invalide: {exc}") from exc


def _unwrap_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise BricoDepotClientError("Réponse JSON : tableau vide.")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise BricoDepotClientError("Réponse JSON : objet attendu.")
    if payload.get("errors"):
        raise BricoDepotGraphQLError(f"Erreurs GraphQL: {payload['errors']!r}")
    return payload


def parse_retailers(payload: Any) -> list[BricoRetailer]:
    root = _unwrap_payload(payload)
    data = root.get("data", root)
    if not isinstance(data, dict):
        raise BricoDepotClientError("champ data invalide")
    retailers = data.get("retailers")
    if not isinstance(retailers, dict):
        raise BricoDepotClientError("data.retailers manquant")
    items = retailers.get("items") or []
    if not isinstance(items, list):
        raise BricoDepotClientError("retailers.items invalide")
    out: list[BricoRetailer] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entity = _as_int(item.get("entity_id"))
        if entity is None:
            continue
        address_data = item.get("address_data") if isinstance(item.get("address_data"), dict) else {}
        coords = address_data.get("coordinates") if isinstance(address_data.get("coordinates"), dict) else {}
        address, city, postcode = retailer_address_fields(
            raw_address=_as_str(item.get("address")),
            address_data=address_data,
        )
        out.append(
            BricoRetailer(
                entity_id=entity,
                seller_code=_as_str(item.get("seller_code")),
                name=_as_str(item.get("name")),
                address=address,
                city=city,
                postcode=postcode,
                latitude=_as_float(coords.get("latitude")),
                longitude=_as_float(coords.get("longitude")),
                phone=_as_str(item.get("contact_phone")),
                distance_km=_as_float(item.get("distance_from_location")),
            )
        )
    return out


def parse_products_by_sku(payload: Any, *, requested_skus: list[str]) -> dict[str, BricoProductOffer]:
    """Indexe les items par SKU exact — ignore l'ordre de retour."""
    root = _unwrap_payload(payload)
    data = root.get("data", root)
    if not isinstance(data, dict):
        raise BricoDepotClientError("champ data invalide")
    products = data.get("products")
    if not isinstance(products, dict):
        # Réponse vide structurée : aucun produit
        return {}
    items = products.get("items") or []
    if not isinstance(items, list):
        raise BricoDepotClientError("products.items invalide")

    by_sku: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sku = _as_str(item.get("sku"))
        if sku and sku not in by_sku:
            by_sku[sku] = item

    wanted = normalize_skus(requested_skus)
    out: dict[str, BricoProductOffer] = {}
    for sku in wanted:
        item = by_sku.get(sku)
        if item is None:
            continue
        out[sku] = BricoProductOffer(
            sku=sku,
            name=_as_str(item.get("name")),
            price_ht_piece=_money_from_final_price(
                _descend(item, "price_range", "minimum_price_excluding_tax", "final_price")
            ),
            price_ttc_piece=_money_from_final_price(
                _descend(item, "price_range", "minimum_price_including_tax", "final_price")
            ),
            price_ht_measure=_money_from_final_price(
                _descend(item, "measure_price", "measure_price_excluding_tax", "final_price")
            ),
            price_ttc_measure=_money_from_final_price(
                _descend(item, "measure_price", "measure_price_including_tax", "final_price")
            ),
            stock_quantity=_as_int(item.get("stock_quantity")),
            stock_status=_as_str(item.get("stock_status")),
            is_salable=_as_bool(item.get("is_salable")),
            is_offer_available=_as_bool(item.get("is_offer_available")),
            raw_item=item,
        )
    return out


def experimental_in_store_available(
    *,
    stock_quantity: int | None,
    is_salable: bool | None,
    is_offer_available: bool | None,
) -> bool:
    return bool(
        stock_quantity is not None
        and stock_quantity > 0
        and is_salable is True
        and is_offer_available is True
    )


class BricoDepotClient:
    def __init__(
        self,
        *,
        endpoint: str = ENDPOINT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: Transport | None = None,
        product_query: str | None = None,
        stores_query: str | None = None,
    ):
        self.endpoint = endpoint
        self.timeout_s = float(timeout_s)
        self.transport = transport or default_transport
        self._product_query = product_query
        self._stores_query = stores_query

    @property
    def product_query(self) -> str:
        if self._product_query is None:
            self._product_query = load_product_query()
        return self._product_query

    @property
    def stores_query(self) -> str:
        if self._stores_query is None:
            self._stores_query = load_stores_query()
        return self._stores_query

    def _post(
        self,
        *,
        query: str,
        query_variables: dict[str, Any],
        headers: dict[str, str],
    ) -> Any:
        payload = [{"query": query, "queryVariables": query_variables}]
        _status, body = self.transport(
            url=self.endpoint,
            payload=payload,
            headers=headers,
            timeout=self.timeout_s,
        )
        return body

    def fetch_retailers(self, insee_code: str) -> list[BricoRetailer]:
        code = str(insee_code).strip()
        if not code:
            raise ValueError("code INSEE requis")
        body = self._post(
            query=self.stores_query,
            query_variables={"code": code},
            headers=build_stores_headers(),
        )
        return parse_retailers(body)

    def fetch_products_by_sku(
        self,
        *,
        seller_id: str | int,
        skus: list[str],
        store_id: str = DEFAULT_STORE_ID,
    ) -> dict[str, BricoProductOffer]:
        sku_list = normalize_skus(skus)
        body = self._post(
            query=self.product_query,
            query_variables={
                "filter": {"sku": {"in": sku_list}},
                "currentPage": 1,
                "pageSize": min(PRODUCT_SKU_PAGE_SIZE, len(sku_list)),
            },
            headers=build_product_headers(seller_id=str(seller_id), store_id=str(store_id)),
        )
        return parse_products_by_sku(body, requested_skus=sku_list)
