"""Probe expérimental Brico Dépôt — dépôts proches d'un code INSEE (hors ProMatConnect).

Isolé : aucune DB, aucun connecteur, aucun crawl national, aucun cookie/session.

Endpoint observé (Firefox) :
  POST https://www.bricodepot.fr/api/magento/customQuery
Body :
  [ { "query": "<GetRetailersFromLocation>", "queryVariables": { "code": "<INSEE>" } } ]

Usage (réseau — à lancer manuellement, 1 commune à la fois) :

  python tools/bricodepot_stores_probe.py --insee 34172

Identifiants (ne pas confondre) :
  entity_id     = id retailer GraphQL (hypothèse : == sellerId du contexte produit)
  seller_code   = code commercial du dépôt
  sellerId      = utilisé dans X-BricoDepot-Context des appels produit (pas ici)
  storeId       = 1 dans les appels produit observés (pas dans cette requête)

Exemple déjà corrélé expérimentalement :
  Dieppe retailer entity_id=133  ↔  produit sellerId=133
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

# Requête GraphQL exacte observée (Firefox) — pageSize:5 comme sur le site.
DEFAULT_GRAPHQL_QUERY = """
query GetRetailersFromLocation($code: String!) {
  retailers(
    filter: { close_to_location: { city_insee_code: $code } }
    pageSize: 5
    currentPage: 1
  ) {
    items {
      address
      address_data {
        city
        city_insee_code
        coordinates {
          latitude
          longitude
        }
        country_id
        postcode
        region
        region_id
        street
      }
      contact_phone
      contact_mail
      entity_id
      information_message_data {
        information_message_is_warning
        information_message_text
        information_message_text_background_color
        information_message_title
        information_message_tooltip_text
      }
      name
      seller_code
      opening {
        current_end_time
        current_timestamp
        next_opening_day
        next_opening_time
        open_now
        weekly_schedule {
          day
          schedule {
            end_time
            start_time
          }
        }
      }
      special_opening {
        date
        is_open
        schedule {
          end_time
          start_time
        }
      }
      distance_from_location
      related_postcodes
      product_offer_stock
      product_offer_is_salable
      url_path_full
    }
    page_info {
      current_page
      is_spellchecked
      page_size
      sort
      total_pages
    }
    total_count
  }
}
""".strip()


@dataclass(frozen=True)
class StoreRecord:
    """Un dépôt retourné par retailers(close_to_location).

    entity_id : identifiant retailer GraphQL.
    hypothesized_seller_id : même valeur que entity_id — hypothèse expérimentale
      (entity_id == sellerId du X-BricoDepot-Context produit), à valider.
    seller_code : code commercial du dépôt (≠ entity_id).
    """

    name: str | None
    entity_id: str | None
    hypothesized_seller_id: str | None
    seller_code: str | None
    address: str | None
    city: str | None
    postcode: str | None
    latitude: float | None
    longitude: float | None
    distance_km: float | None
    phone: str | None
    email: str | None
    url_path_full: str | None


@dataclass(frozen=True)
class StoresProbeResult:
    insee_code: str
    total_count: int | None
    stores: tuple[StoreRecord, ...]


class StoresProbeError(Exception):
    """Erreur métier du probe dépôts (HTTP, JSON, GraphQL…)."""


def load_graphql_query(path: Path | None = None) -> str:
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    else:
        here = Path(__file__).resolve().parent
        root = here.parent
        candidates.append(here / "bricodepot_stores_query.graphql.local")
        candidates.append(
            root / "app" / "connectors" / "bricodepot" / "queries" / "stores.graphql"
        )
        candidates.append(here / "bricodepot_stores_query.graphql")
    for candidate in candidates:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text and ("{" in text) and ("retailers" in text or "query" in text.lower()):
                lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
                cleaned = "\n".join(lines).strip()
                if cleaned and "{" in cleaned:
                    return cleaned
    return DEFAULT_GRAPHQL_QUERY


def build_payload(*, insee_code: str, query: str | None = None) -> list[dict[str, Any]]:
    """Corps HTTP exact : tableau JSON d'une opération customQuery."""
    return [
        {
            "query": query or DEFAULT_GRAPHQL_QUERY,
            "queryVariables": {"code": str(insee_code)},
        }
    ]


def build_request_headers() -> dict[str, str]:
    """Aucun cookie / aucune session / pas de X-BricoDepot-Context pour ce probe."""
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ProMatConnect-BricoDepotStoresProbe/0.1 (+experimental; no-session)",
    }


def extract_retailers_block(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise StoresProbeError("Réponse JSON : tableau vide.")
        payload = payload[0]

    if not isinstance(payload, dict):
        raise StoresProbeError("Réponse JSON : objet attendu.")
    if payload.get("errors"):
        raise StoresProbeError(f"Erreurs GraphQL: {payload['errors']!r}")

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise StoresProbeError("Réponse JSON : champ data invalide.")

    retailers = data.get("retailers")
    if not isinstance(retailers, dict):
        raise StoresProbeError("Impossible de trouver data.retailers dans la réponse.")
    return retailers


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


def parse_store_item(item: dict[str, Any]) -> StoreRecord:
    address_data = item.get("address_data")
    if not isinstance(address_data, dict):
        address_data = {}
    coords = address_data.get("coordinates")
    if not isinstance(coords, dict):
        coords = {}

    entity_raw = item.get("entity_id")
    entity_id = _as_str(entity_raw)
    # Hypothèse expérimentale : entity_id == sellerId (contexte produit).
    # On conserve les deux noms jusqu'à validation supplémentaire.
    hypothesized_seller_id = entity_id

    street = _as_str(address_data.get("street"))
    address = _as_str(item.get("address")) or street

    return StoreRecord(
        name=_as_str(item.get("name")),
        entity_id=entity_id,
        hypothesized_seller_id=hypothesized_seller_id,
        seller_code=_as_str(item.get("seller_code")),
        address=address,
        city=_as_str(address_data.get("city")),
        postcode=_as_str(address_data.get("postcode")),
        latitude=_as_float(coords.get("latitude")),
        longitude=_as_float(coords.get("longitude")),
        distance_km=_as_float(item.get("distance_from_location")),
        phone=_as_str(item.get("contact_phone")),
        email=_as_str(item.get("contact_mail")),
        url_path_full=_as_str(item.get("url_path_full")),
    )


def parse_response(payload: Any, *, insee_code: str) -> StoresProbeResult:
    retailers = extract_retailers_block(payload)
    raw_items = retailers.get("items")
    if raw_items is None:
        items: list[dict[str, Any]] = []
    elif isinstance(raw_items, list):
        items = [i for i in raw_items if isinstance(i, dict)]
    else:
        raise StoresProbeError("retailers.items doit être une liste.")

    total = retailers.get("total_count")
    total_count: int | None
    if total is None:
        total_count = None
    else:
        try:
            total_count = int(total)
        except (TypeError, ValueError) as exc:
            raise StoresProbeError(f"total_count invalide: {total!r}") from exc

    stores = tuple(parse_store_item(item) for item in items)
    return StoresProbeResult(insee_code=str(insee_code), total_count=total_count, stores=stores)


def _format_distance(km: float | None) -> str:
    if km is None:
        return "—"
    try:
        q = Decimal(str(km)).quantize(Decimal("0.01"))
    except Exception:
        q = km
    return f"{q} km"


def _format_coord(value: float | None) -> str:
    if value is None:
        return "—"
    return str(value)


def format_stores(result: StoresProbeResult) -> str:
    lines = [
        "BRICO DEPOT STORES PROBE",
        f"INSEE: {result.insee_code}",
        f"total_count: {result.total_count if result.total_count is not None else '—'}",
        f"items: {len(result.stores)}",
        "",
        "Note: entity_id ≈ sellerId (hypothèse expérimentale, non validée partout).",
        "      seller_code = code commercial ; storeId produit observé = 1.",
        "",
    ]
    if not result.stores:
        lines.append("(aucun dépôt)")
        return "\n".join(lines)

    for idx, store in enumerate(result.stores, start=1):
        lines.extend(
            [
                f"{idx}. {store.name or '—'}",
                f"   entity_id: {store.entity_id or '—'}",
                f"   hypothesized_seller_id: {store.hypothesized_seller_id or '—'}",
                f"   seller_code: {store.seller_code or '—'}",
                f"   adresse: {store.address or '—'}",
                f"   ville: {store.city or '—'}",
                f"   CP: {store.postcode or '—'}",
                f"   latitude: {_format_coord(store.latitude)}",
                f"   longitude: {_format_coord(store.longitude)}",
                f"   distance: {_format_distance(store.distance_km)}",
                f"   téléphone: {store.phone or '—'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def result_to_jsonable(result: StoresProbeResult) -> dict[str, Any]:
    return {
        "insee_code": result.insee_code,
        "total_count": result.total_count,
        "stores": [asdict(s) for s in result.stores],
        "identity_notes": {
            "entity_id": "id retailer GraphQL",
            "hypothesized_seller_id": (
                "copie de entity_id — hypothèse entity_id == sellerId produit"
            ),
            "seller_code": "code commercial du dépôt",
            "storeId": "1 dans les appels produit observés (absent de cette requête)",
        },
    }


def http_post_json(
    *,
    url: str,
    payload: list[dict[str, Any]] | dict[str, Any],
    headers: dict[str, str],
    timeout: float = 30.0,
) -> tuple[int, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        raise StoresProbeError(
            f"HTTP {exc.code}: {(raw[:800] or b'').decode('utf-8', errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise StoresProbeError(f"Erreur réseau: {exc.reason}") from exc

    if status != 200:
        raise StoresProbeError(f"HTTP {status}: {raw[:800]!r}")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise StoresProbeError(f"JSON invalide: {exc}") from exc


def run_stores_probe(
    *,
    insee_code: str,
    dry_run: bool = False,
    query_path: Path | None = None,
    fixture_payload: Any | None = None,
) -> tuple[dict[str, Any], StoresProbeResult | None]:
    query = load_graphql_query(query_path)
    payload = build_payload(insee_code=insee_code, query=query)
    headers = build_request_headers()
    meta = {
        "endpoint": ENDPOINT,
        "http_status": None,
        "headers_sent": headers,
        "body_is_array": True,
        "queryVariables": payload[0].get("queryVariables"),
        "query_chars": len(payload[0].get("query") or ""),
        "cookies": None,
        "note": (
            "Aucun cookie/session. "
            "entity_id (retailer) hypothétiquement == sellerId (contexte produit)."
        ),
    }
    if dry_run:
        return meta, None
    if fixture_payload is not None:
        meta["http_status"] = 200
        meta["source"] = "fixture"
        result = parse_response(fixture_payload, insee_code=insee_code)
        return meta, result

    status, response = http_post_json(url=ENDPOINT, payload=payload, headers=headers)
    meta["http_status"] = status
    result = parse_response(response, insee_code=insee_code)
    return meta, result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe expérimental Brico Dépôt — retailers proches d'un code INSEE "
            "(hors ProMatConnect)."
        )
    )
    parser.add_argument(
        "--insee",
        required=True,
        help="Code INSEE commune (ex: 34172 pour Montpellier)",
    )
    parser.add_argument("--json", action="store_true", help="Ajoute une sortie JSON")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'appelle pas le réseau : affiche headers/payload",
    )
    parser.add_argument(
        "--from-fixture",
        type=Path,
        default=None,
        help="Parse une réponse JSON locale (hors réseau)",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        default=None,
        help="Fichier GraphQL local (sinon défaut / .local gitignoré)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fixture_payload = None
    if args.from_fixture is not None:
        try:
            fixture_payload = json.loads(args.from_fixture.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"ERREUR: fixture introuvable: {args.from_fixture}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"ERREUR: JSON invalide: {exc}", file=sys.stderr)
            return 1

    try:
        meta, result = run_stores_probe(
            insee_code=args.insee,
            dry_run=args.dry_run,
            query_path=args.query_file,
            fixture_payload=fixture_payload,
        )
    except (StoresProbeError, ValueError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    print("=== Requête (safe) ===")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print()
    if args.dry_run:
        print("Dry-run : aucune requête réseau envoyée.")
        print(json.dumps(build_payload(insee_code=args.insee)[0]["queryVariables"], indent=2))
        return 0

    assert result is not None
    print(format_stores(result))
    if args.json:
        print()
        print(json.dumps(result_to_jsonable(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
