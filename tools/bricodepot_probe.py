"""Probe expérimental Brico Dépôt — API publique customQuery (hors navigateur).

Isolé de ProMatConnect : aucune DB, aucun connecteur, aucun import CSV.

Payload HTTP observé (Firefox) : TABLEAU JSON
  [ { "query": "...", "queryVariables": { filter.sku.in, currentPage, pageSize } } ]

Le dépôt n'est PAS dans queryVariables — transmis via
X-BricoDepot-Context et/ou cookies sellerId/storeId/sellerCode.

Usage (réseau — à lancer manuellement, 1 variante à la fois) :

  python tools/bricodepot_probe.py \\
    --sku 3334160524579 --seller-id 10 --seller-code 2350 --variant A

  python tools/bricodepot_probe.py \\
    --sku 3334160524579 --sku AUTRE_SKU --seller-id 10 --seller-code 2350 --variant A

  python tools/bricodepot_probe.py \\
    --sku 3334160524579 --seller-id 133 --seller-code 2362 --variant A

Variantes :
  A = X-BricoDepot-Context seul (aucun cookie)
  B = Context + cookies sellerId, storeId
  C = Context + cookies sellerId, storeId, sellerCode
  D = cookies sellerId, storeId, sellerCode (sans header Context)

Ne jamais coller de cookies Didomi / Datadog / cartId / consent.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

ENDPOINT = "https://www.bricodepot.fr/api/magento/customQuery"

# Requête GraphQL alignée Firefox (voir tools/bricodepot_query.graphql).
# $postCode déclaré (comme sur le site) mais volontairement omis de queryVariables.
# Fallback embarqué si le fichier .graphql est absent.
DEFAULT_GRAPHQL_QUERY = """
query ProductBySku(
  $filter: ProductAttributeFilterInput
  $currentPage: Int
  $pageSize: Int
  $postCode: String
) {
  products(filter: $filter, currentPage: $currentPage, pageSize: $pageSize) {
    items {
      ...ProductCoreFragment
      ...ProductPriceFragment
      ...ProductMeasurePriceFragment
    }
  }
}

fragment PriceRangeFragment on ProductPrice {
  discount {
    amount_off
    percent_off
  }
  final_price {
    currency
    value
  }
  regular_price {
    currency
    value
  }
}

fragment ProductPriceFragment on ProductInterface {
  uid
  type_of_rounding_value
  price_range {
    minimum_price {
      ...PriceRangeFragment
    }
    minimum_price_excluding_tax {
      ...PriceRangeFragment
    }
    minimum_price_including_tax {
      ...PriceRangeFragment
    }
    special_price_excluding_tax {
      ...PriceRangeFragment
    }
    special_price_including_tax {
      ...PriceRangeFragment
    }
  }
}

fragment ProductMeasurePriceFragment on ProductInterface {
  measure_price {
    measure_price_including_tax {
      final_price {
        currency
        value
      }
      regular_price {
        currency
        value
      }
    }
    measure_price_excluding_tax {
      final_price {
        currency
        value
      }
      regular_price {
        currency
        value
      }
    }
  }
}

fragment ProductCoreFragment on ProductInterface {
  uid
  id
  name
  sku
  ean_code
  sap_easier_code
  images {
    role
    url
  }
  conditionnement_label
  content_net_value
  content_net_unit_label
  stock_status
  stock_quantity
  is_salable
  is_offer_available
  is_eligible_for_click_and_collect
  is_eligible_for_delivery(postcode: $postCode)
  estimated_delivery_date
}
""".strip()

# Alias historiques → lettres A–D
VARIANT_ALIASES = {
    "A": "A",
    "B": "B",
    "C": "C",
    "D": "D",
    "header": "A",
    "header+cookies": "B",
    "header+cookies+code": "C",
    "cookies": "D",
}

VARIANTS = ("A", "B", "C", "D")

SENSITIVE_COOKIE_PREFIXES = (
    "didomi",
    "_dd",
    "citrus",
    "cart",
    "session",
    "consent",
    "datadog",
    "bvbrand",
    "euconsent",
)


@dataclass(frozen=True)
class Money:
    value: Decimal | None
    currency: str | None = None


@dataclass(frozen=True)
class ProbeSummary:
    sku: str
    seller_id: str
    seller_code: str
    store_id: str
    name: str | None
    conditionnement_label: str | None
    content_net_value: str | None
    content_net_unit_label: str | None
    price_ht_piece: Money
    price_ttc_piece: Money
    price_ht_measure: Money
    price_ttc_measure: Money
    price_range_raw: dict[str, Any]
    stock_status: str | None
    stock_quantity: int | None
    is_salable: bool | None
    is_offer_available: bool | None
    click_collect: bool | None
    delivery: bool | None
    available_in_store: bool | None
    available_in_store_rule: str


class ProbeError(Exception):
    """Erreur métier du probe (HTTP, JSON, SKU absent…)."""


def normalize_variant(raw: str) -> str:
    key = str(raw).strip()
    if key not in VARIANT_ALIASES:
        raise ValueError(
            f"Variante inconnue: {raw!r}. Choisir parmi {VARIANTS} "
            f"(ou alias header / header+cookies / header+cookies+code / cookies)."
        )
    return VARIANT_ALIASES[key]


def build_context_header(*, seller_id: str, store_id: str) -> str:
    return (
        f"customerGroupId=0;customerSegmentIds=1;isLoggedIn=0;"
        f"sellerId={seller_id};storeId={store_id}"
    )


def build_cookie_header(
    *,
    seller_id: str,
    store_id: str,
    seller_code: str | None = None,
) -> str:
    parts = [f"sellerId={seller_id}", f"storeId={store_id}"]
    if seller_code is not None and str(seller_code).strip() != "":
        parts.append(f"sellerCode={seller_code}")
    return "; ".join(parts)


def build_request_headers(
    *,
    variant: str,
    seller_id: str,
    seller_code: str,
    store_id: str,
) -> dict[str, str]:
    variant = normalize_variant(variant)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ProMatConnect-BricoDepotProbe/0.1 (+experimental; no-session)",
    }
    context = build_context_header(seller_id=seller_id, store_id=store_id)

    if variant == "A":
        headers["X-BricoDepot-Context"] = context
    elif variant == "B":
        headers["X-BricoDepot-Context"] = context
        headers["Cookie"] = build_cookie_header(
            seller_id=seller_id, store_id=store_id, seller_code=None
        )
    elif variant == "C":
        headers["X-BricoDepot-Context"] = context
        headers["Cookie"] = build_cookie_header(
            seller_id=seller_id, store_id=store_id, seller_code=seller_code
        )
    elif variant == "D":
        headers["Cookie"] = build_cookie_header(
            seller_id=seller_id, store_id=store_id, seller_code=seller_code
        )
    return headers


def load_graphql_query(path: Path | None = None) -> str:
    """Charge une requête locale si présente, sinon la source canonique app/, sinon embarquée."""
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    else:
        here = Path(__file__).resolve().parent
        root = here.parent
        candidates.append(here / "bricodepot_query.graphql.local")
        # Source partagée avec le connecteur (évite deux GraphQL divergents).
        candidates.append(
            root / "app" / "connectors" / "bricodepot" / "queries" / "product.graphql"
        )
        candidates.append(here / "bricodepot_query.graphql")
    for candidate in candidates:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text and ("{" in text) and ("products" in text or "query" in text.lower()):
                lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
                cleaned = "\n".join(lines).strip()
                if cleaned and "{" in cleaned:
                    return cleaned
    return DEFAULT_GRAPHQL_QUERY


def normalize_skus(*skus: str, sku_list: list[str] | None = None) -> list[str]:
    """Liste de SKU dédupliquée, ordre d'entrée conservé."""
    raw: list[str] = []
    if sku_list:
        raw.extend(sku_list)
    raw.extend(skus)
    ordered: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    if not ordered:
        raise ValueError("Au moins un SKU non vide est requis.")
    return ordered


def build_query_variables(
    *,
    sku: str | None = None,
    skus: list[str] | None = None,
    page_size: int | None = None,
    current_page: int = 1,
) -> dict[str, Any]:
    """Variables observées — sans sellerId / sans postCode.

    filter.sku.in accepte un ou plusieurs SKU.
    """
    sku_list = normalize_skus(*( [sku] if sku is not None else [] ), sku_list=skus)
    size = int(page_size) if page_size is not None else max(10, len(sku_list))
    return {
        "filter": {"sku": {"in": sku_list}},
        "currentPage": int(current_page),
        "pageSize": size,
    }


def build_payload(
    *,
    sku: str | None = None,
    skus: list[str] | None = None,
    query: str | None = None,
    page_size: int | None = None,
) -> list[dict[str, Any]]:
    """Corps HTTP exact : tableau JSON d'une opération customQuery."""
    return [
        {
            "query": query or DEFAULT_GRAPHQL_QUERY,
            "queryVariables": build_query_variables(
                sku=sku, skus=skus, page_size=page_size
            ),
        }
    ]


def extract_items(payload: Any) -> list[dict[str, Any]]:
    """Extrait products.items depuis une réponse Magento / customQuery."""
    # Réponse parfois enveloppe tableau (miroir du body).
    if isinstance(payload, list):
        if not payload:
            raise ProbeError("Réponse JSON : tableau vide.")
        payload = payload[0]

    if not isinstance(payload, dict):
        raise ProbeError("Réponse JSON : objet attendu.")
    if "errors" in payload and payload["errors"]:
        raise ProbeError(f"Erreurs GraphQL: {payload['errors']!r}")

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ProbeError("Réponse JSON : champ data invalide.")

    products = data.get("products")
    if isinstance(products, dict) and isinstance(products.get("items"), list):
        return [i for i in products["items"] if isinstance(i, dict)]

    for key in ("items", "product"):
        raw = data.get(key)
        if isinstance(raw, list):
            return [i for i in raw if isinstance(i, dict)]
        if isinstance(raw, dict):
            return [raw]
    raise ProbeError("Impossible de trouver products.items dans la réponse.")


def find_product_by_sku(items: list[dict[str, Any]], sku: str) -> dict[str, Any]:
    """Égalité exacte de SKU — jamais items[0] par défaut."""
    if not items:
        raise ProbeError("Aucun item dans la réponse.")
    matches = [item for item in items if str(item.get("sku", "")) == str(sku)]
    if not matches:
        skus = [str(i.get("sku")) for i in items[:10]]
        raise ProbeError(f"SKU {sku!r} absent de la réponse. SKUs vus (max 10): {skus}")
    return matches[0]


def _as_money_value(raw: Any, currency: Any = None) -> Money:
    if raw is None:
        return Money(value=None, currency=str(currency) if currency else None)
    try:
        return Money(
            value=Decimal(str(raw)),
            currency=str(currency) if currency else None,
        )
    except Exception:
        return Money(value=None, currency=str(currency) if currency else None)


def _money_from_branch(branch: Any, *, _depth: int = 0) -> Money:
    """Lit un nœud prix Magento/ADEO (final_price | regular_price | amount | value)."""
    if _depth > 4:
        return Money(value=None, currency=None)
    if isinstance(branch, list):
        for entry in branch:
            got = _money_from_branch(entry, _depth=_depth + 1)
            if got.value is not None:
                return got
        return Money(value=None, currency=None)
    if not isinstance(branch, dict):
        return Money(value=None, currency=None)

    # Forme plate : { value, currency }
    if branch.get("value") is not None:
        return _as_money_value(branch.get("value"), branch.get("currency"))

    # Forme observée Firefox / ADEO : { final_price: { value, currency } }
    # Variantes : regular_price, amount, price ; amount peut envelopper value.
    for key in ("final_price", "regular_price", "amount", "price"):
        nested = branch.get(key)
        if nested is None:
            continue
        got = _money_from_branch(nested, _depth=_depth + 1)
        if got.value is not None:
            return got

    return Money(value=None, currency=None)


def _descend(root: Any, *path: str) -> Any:
    cur: Any = root
    for key in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _nested_money(root: Any, *path: str) -> Money:
    return _money_from_branch(_descend(root, *path))


PRICE_RANGE_BRANCHES = (
    "minimum_price",
    "minimum_price_excluding_tax",
    "minimum_price_including_tax",
    "special_price_excluding_tax",
    "special_price_including_tax",
)


def _money_to_raw(money: Money) -> dict[str, Any]:
    return {
        "value": str(money.value) if money.value is not None else None,
        "currency": money.currency,
    }


def inspect_price_range_raw(item: dict[str, Any]) -> dict[str, Any]:
    """Extrait brut de tous les nœuds price_range Firefox — sans calcul."""
    price_range = item.get("price_range")
    out: dict[str, Any] = {}
    if not isinstance(price_range, dict):
        return out
    for branch_name in PRICE_RANGE_BRANCHES:
        branch = price_range.get(branch_name)
        if branch is None:
            out[branch_name] = None
            continue
        final_m = _nested_money(price_range, branch_name, "final_price")
        # Si final_price.value est null, ne pas basculer silencieusement sur regular_price
        # pour le « final » : lire explicitement chaque clé.
        final_node = _descend(price_range, branch_name, "final_price")
        regular_node = _descend(price_range, branch_name, "regular_price")
        discount = _descend(price_range, branch_name, "discount")
        out[branch_name] = {
            "final_price": (
                _money_to_raw(_as_money_value(
                    final_node.get("value") if isinstance(final_node, dict) else None,
                    final_node.get("currency") if isinstance(final_node, dict) else None,
                ))
                if final_node is not None
                else None
            ),
            "regular_price": (
                _money_to_raw(_as_money_value(
                    regular_node.get("value") if isinstance(regular_node, dict) else None,
                    regular_node.get("currency") if isinstance(regular_node, dict) else None,
                ))
                if regular_node is not None
                else None
            ),
            "discount": discount if isinstance(discount, dict) else None,
            # Aide diagnostic : valeur « effective » si final non null, sinon None
            # (jamais regular_price promu en final pour inventer un prix pièce).
            "final_price_resolved": _money_to_raw(final_m)
            if isinstance(final_node, dict) and final_node.get("value") is not None
            else None,
        }
    return out


def extract_piece_and_measure_prices(item: dict[str, Any]) -> tuple[Money, Money, Money, Money]:
    """Prix pièce + prix mesure — valeurs API explicites uniquement (jamais m² × mesure).

    Priorité pièce (Firefox) :
      HT  = price_range.minimum_price_excluding_tax.final_price
      TTC = price_range.minimum_price_including_tax.final_price

    Les autres branches (minimum_price, special_price_*) sont inspectées via
    inspect_price_range_raw — jamais utilisées pour inventer HT/TTC pièce.

    Ne jamais dériver HT/TTC pièce depuis measure_price × content_net_value.
    Ne jamais reconstruire HT depuis TTC (ni l'inverse).
    """
    excl = _descend(item, "price_range", "minimum_price_excluding_tax", "final_price")
    incl = _descend(item, "price_range", "minimum_price_including_tax", "final_price")
    # Lit final_price (value plat, ou wrapper amount) — jamais regular_price.
    price_ht_piece = _money_from_branch(excl)
    price_ttc_piece = _money_from_branch(incl)

    price_ht_measure = _nested_money(
        item, "measure_price", "measure_price_excluding_tax", "final_price"
    )
    if price_ht_measure.value is None:
        price_ht_measure = _nested_money(item, "measure_price", "measure_price_excluding_tax")
    price_ttc_measure = _nested_money(
        item, "measure_price", "measure_price_including_tax", "final_price"
    )
    if price_ttc_measure.value is None:
        price_ttc_measure = _nested_money(item, "measure_price", "measure_price_including_tax")
    return price_ht_piece, price_ttc_piece, price_ht_measure, price_ttc_measure


def parse_stock_quantity(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(raw)))
        except Exception as exc:
            raise ProbeError(f"stock_quantity invalide: {raw!r}") from exc


def experimental_available_in_store(
    *,
    stock_quantity: int | None,
    is_salable: bool | None,
    is_offer_available: bool | None,
) -> bool | None:
    """Règle ProMatConnect expérimentale — pas une règle officielle Brico Dépôt."""
    if stock_quantity is None or is_salable is None or is_offer_available is None:
        return None
    return bool(stock_quantity > 0 and is_salable is True and is_offer_available is True)


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def summarize_product(
    item: dict[str, Any],
    *,
    sku: str,
    seller_id: str,
    seller_code: str,
    store_id: str,
) -> ProbeSummary:
    qty = parse_stock_quantity(item.get("stock_quantity"))
    is_salable = _as_bool(item.get("is_salable"))
    is_offer = _as_bool(item.get("is_offer_available"))
    click = _as_bool(item.get("is_eligible_for_click_and_collect"))
    delivery = _as_bool(item.get("is_eligible_for_delivery"))
    available = experimental_available_in_store(
        stock_quantity=qty,
        is_salable=is_salable,
        is_offer_available=is_offer,
    )
    price_ht_piece, price_ttc_piece, price_ht_measure, price_ttc_measure = (
        extract_piece_and_measure_prices(item)
    )
    price_range_raw = inspect_price_range_raw(item)

    return ProbeSummary(
        sku=sku,
        seller_id=str(seller_id),
        seller_code=str(seller_code),
        store_id=str(store_id),
        name=str(item["name"]) if item.get("name") is not None else None,
        conditionnement_label=(
            str(item["conditionnement_label"])
            if item.get("conditionnement_label") is not None
            else None
        ),
        content_net_value=(
            str(item["content_net_value"]) if item.get("content_net_value") is not None else None
        ),
        content_net_unit_label=(
            str(item["content_net_unit_label"])
            if item.get("content_net_unit_label") is not None
            else None
        ),
        price_ht_piece=price_ht_piece,
        price_ttc_piece=price_ttc_piece,
        price_ht_measure=price_ht_measure,
        price_ttc_measure=price_ttc_measure,
        price_range_raw=price_range_raw,
        stock_status=str(item["stock_status"]) if item.get("stock_status") is not None else None,
        stock_quantity=qty,
        is_salable=is_salable,
        is_offer_available=is_offer,
        click_collect=click,
        delivery=delivery,
        available_in_store=available,
        available_in_store_rule=(
            "expérimental ProMatConnect: "
            "stock_quantity > 0 AND is_salable AND is_offer_available "
            "(ne pas s'appuyer sur stock_status seul)"
        ),
    )


@dataclass(frozen=True)
class MultiProbeResult:
    """Résultats multi-SKU indexés par SKU demandé (ordre d'entrée)."""

    requested_skus: tuple[str, ...]
    found: tuple[ProbeSummary, ...]
    missing_skus: tuple[str, ...]

    @property
    def by_sku(self) -> dict[str, ProbeSummary]:
        return {s.sku: s for s in self.found}


def parse_multi_response(
    payload: Any,
    *,
    skus: list[str],
    seller_id: str,
    seller_code: str,
    store_id: str,
) -> MultiProbeResult:
    """Parcourt tous les items ; association par égalité exacte de SKU.

    N'utilise jamais l'ordre de retour comme ordre demandé.
    Signale les SKU demandés absents sans inventer de prix.
    """
    requested = tuple(normalize_skus(sku_list=skus))
    items = extract_items(payload)
    by_sku: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("sku", "")).strip()
        if key and key not in by_sku:
            by_sku[key] = item

    found: list[ProbeSummary] = []
    missing: list[str] = []
    for sku in requested:
        item = by_sku.get(sku)
        if item is None:
            missing.append(sku)
            continue
        found.append(
            summarize_product(
                item,
                sku=sku,
                seller_id=seller_id,
                seller_code=seller_code,
                store_id=store_id,
            )
        )
    return MultiProbeResult(
        requested_skus=requested,
        found=tuple(found),
        missing_skus=tuple(missing),
    )


def parse_response(
    payload: Any,
    *,
    sku: str,
    seller_id: str,
    seller_code: str,
    store_id: str,
) -> ProbeSummary:
    """Comportement historique mono-SKU : erreur si SKU absent / items vides."""
    items = extract_items(payload)
    item = find_product_by_sku(items, sku)
    return summarize_product(
        item,
        sku=sku,
        seller_id=seller_id,
        seller_code=seller_code,
        store_id=store_id,
    )


def format_multi_summary(result: MultiProbeResult) -> str:
    lines = [
        "BRICO DEPOT PROBE (multi-SKU)",
        f"Demandés: {len(result.requested_skus)}",
        f"Trouvés: {len(result.found)}",
        f"Absents: {len(result.missing_skus)}",
    ]
    if result.missing_skus:
        lines.append(f"SKU absents: {', '.join(result.missing_skus)}")
    lines.append("")
    if not result.found:
        lines.append("(aucun produit trouvé)")
        return "\n".join(lines)
    for index, summary in enumerate(result.found, start=1):
        lines.append(f"===== [{index}/{len(result.found)}] =====")
        lines.append(format_summary(summary))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_money(m: Money) -> str:
    if m.value is None:
        return "—"
    cur = m.currency or "EUR"
    try:
        q = m.value.quantize(Decimal("0.01"))
    except Exception:
        q = m.value
    return f"{q} {cur}"


def format_summary(summary: ProbeSummary) -> str:
    lines = [
        "BRICO DEPOT PROBE",
        f"SKU: {summary.sku}",
        f"Seller ID: {summary.seller_id}",
        f"Seller Code: {summary.seller_code}",
        f"Store ID: {summary.store_id}",
        "",
        "Produit:",
        summary.name or "—",
        "",
        "Conditionnement:",
        summary.conditionnement_label or "—",
    ]
    if summary.content_net_value is not None or summary.content_net_unit_label is not None:
        lines.append(
            f"{summary.content_net_value or '—'} {summary.content_net_unit_label or ''}".strip()
        )
    lines.extend(
        [
            "",
            "Prix:",
            f"HT pièce: {_format_money(summary.price_ht_piece)}",
            f"TTC pièce: {_format_money(summary.price_ttc_piece)}",
            f"HT unité de mesure: {_format_money(summary.price_ht_measure)}",
            f"TTC unité de mesure: {_format_money(summary.price_ttc_measure)}",
            f"price_range_raw branches: {', '.join(summary.price_range_raw.keys()) or '—'}",
            "",
            "Stock:",
            f"stock_status: {summary.stock_status}",
            f"stock_quantity: {summary.stock_quantity}",
            f"is_salable: {summary.is_salable}",
            f"is_offer_available: {summary.is_offer_available}",
            f"click_collect: {summary.click_collect}",
            f"delivery: {summary.delivery}",
            "",
            "Disponibilité (règle expérimentale ProMatConnect):",
            f"available_in_store: {summary.available_in_store}",
            f"règle: {summary.available_in_store_rule}",
        ]
    )
    return "\n".join(lines)


def summarize_to_jsonable(summary: ProbeSummary) -> dict[str, Any]:
    data = asdict(summary)
    for key in (
        "price_ht_piece",
        "price_ttc_piece",
        "price_ht_measure",
        "price_ttc_measure",
    ):
        money = data[key]
        data[key] = {
            "value": str(money["value"]) if money["value"] is not None else None,
            "currency": money["currency"],
        }
    return data


def redact_headers_for_log(headers: dict[str, str]) -> dict[str, str]:
    out = dict(headers)
    if "Cookie" in out:
        names = []
        for part in out["Cookie"].split(";"):
            name = part.strip().split("=", 1)[0].strip()
            if name:
                names.append(name)
        out["Cookie"] = f"<redacted names={names}>"
    return out


def assert_no_sensitive_cookies(cookie_header: str | None) -> None:
    if not cookie_header:
        return
    lowered = cookie_header.lower()
    for prefix in SENSITIVE_COOKIE_PREFIXES:
        if prefix in lowered:
            raise ProbeError(
                f"Cookie sensible détecté ({prefix}…). "
                "N'utilisez que sellerId / storeId / sellerCode."
            )


def http_post_json(
    *,
    url: str,
    payload: list[dict[str, Any]] | dict[str, Any],
    headers: dict[str, str],
    timeout: float = 30.0,
) -> tuple[int, Any]:
    assert_no_sensitive_cookies(headers.get("Cookie"))
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        raise ProbeError(
            f"HTTP {exc.code}: {(raw[:800] or b'').decode('utf-8', errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ProbeError(f"Erreur réseau: {exc.reason}") from exc

    if status != 200:
        raise ProbeError(f"HTTP {status}: {raw[:800]!r}")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ProbeError(f"JSON invalide: {exc}") from exc


def run_probe(
    *,
    sku: str | None = None,
    skus: list[str] | None = None,
    seller_id: str,
    seller_code: str,
    store_id: str,
    variant: str = "A",
    dry_run: bool = False,
    query_path: Path | None = None,
    save_raw: Path | None = None,
) -> tuple[dict[str, Any], MultiProbeResult | None]:
    variant = normalize_variant(variant)
    sku_list = normalize_skus(*( [sku] if sku is not None else [] ), sku_list=skus)
    query = load_graphql_query(query_path)
    payload = build_payload(skus=sku_list, query=query)
    headers = build_request_headers(
        variant=variant,
        seller_id=seller_id,
        seller_code=seller_code,
        store_id=store_id,
    )
    op0 = payload[0]
    meta = {
        "endpoint": ENDPOINT,
        "variant": variant,
        "http_status": None,
        "headers_sent": redact_headers_for_log(headers),
        "body_is_array": True,
        "queryVariables": op0.get("queryVariables"),
        "sku_count": len(sku_list),
        "query_chars": len(op0.get("query") or ""),
        "query_has_price_range": "price_range" in (op0.get("query") or ""),
        "query_has_minimum_price_excluding_tax": (
            "minimum_price_excluding_tax" in (op0.get("query") or "")
        ),
        "note": (
            "sellerId/storeId absents de queryVariables — "
            "transmis via X-BricoDepot-Context et/ou cookies uniquement"
        ),
    }
    if dry_run:
        return meta, None
    status, response = http_post_json(url=ENDPOINT, payload=payload, headers=headers)
    meta["http_status"] = status
    if save_raw is not None:
        save_raw.write_text(
            json.dumps(response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        meta["saved_raw"] = str(save_raw)
    result = parse_multi_response(
        response,
        skus=sku_list,
        seller_id=seller_id,
        seller_code=seller_code,
        store_id=store_id,
    )
    meta["found_count"] = len(result.found)
    meta["missing_skus"] = list(result.missing_skus)
    return meta, result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe expérimental Brico Dépôt (customQuery) — hors ProMatConnect."
    )
    parser.add_argument(
        "--sku",
        action="append",
        required=True,
        dest="skus",
        help="SKU produit (répétable : --sku A --sku B)",
    )
    parser.add_argument("--seller-id", required=True, help="sellerId du dépôt")
    parser.add_argument("--seller-code", required=True, help="sellerCode du dépôt")
    parser.add_argument("--store-id", default="1", help="storeId (défaut: 1)")
    parser.add_argument(
        "--variant",
        default="A",
        help="A | B | C | D (voir docstring). Une seule variante par exécution.",
    )
    parser.add_argument("--json", action="store_true", help="Ajoute une sortie JSON du résumé")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'appelle pas le réseau : affiche headers/payload (cookies redactés)",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        default=None,
        help="Fichier GraphQL local (sinon défaut / .local gitignoré)",
    )
    parser.add_argument(
        "--save-raw",
        type=Path,
        default=None,
        help="Enregistre la réponse JSON brute (diagnostic prix) — ne pas committer",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        meta, result = run_probe(
            skus=args.skus,
            seller_id=args.seller_id,
            seller_code=args.seller_code,
            store_id=args.store_id,
            variant=args.variant,
            dry_run=args.dry_run,
            query_path=args.query_file,
            save_raw=args.save_raw,
        )
    except (ProbeError, ValueError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    print("=== Requête (safe) ===")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print()
    if args.dry_run:
        print("Dry-run : aucune requête réseau envoyée.")
        return 0
    print(f"HTTP status: {meta.get('http_status')}")
    print()
    assert result is not None
    print(format_multi_summary(result))
    if result.missing_skus:
        print(
            f"ATTENTION: SKU absents de la réponse: {', '.join(result.missing_skus)}",
            file=sys.stderr,
        )
    for summary in result.found:
        if (
            summary.price_ht_piece.value is None
            and summary.price_ttc_piece.value is None
            and (
                summary.price_ht_measure.value is not None
                or summary.price_ttc_measure.value is not None
            )
        ):
            print(
                f"\nNOTE [{summary.sku}]: prix pièce absents alors que prix mesure présents. "
                "Ne jamais déduire pièce = mesure × surface.",
                file=sys.stderr,
            )
    if args.json:
        print()
        print(
            json.dumps(
                {
                    "requested_skus": list(result.requested_skus),
                    "missing_skus": list(result.missing_skus),
                    "found": [summarize_to_jsonable(s) for s in result.found],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    # Exit 2 si au moins un SKU demandé est absent (tout en affichant les trouvés).
    return 2 if result.missing_skus else 0


if __name__ == "__main__":
    raise SystemExit(main())
