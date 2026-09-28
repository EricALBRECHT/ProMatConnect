"""Sonde lecture seule du catalogue public Gedimat. N'écrit pas en base.

Usage :
  python tools/gedimat_catalog_probe.py --store-id 2069 --out /tmp/gedimat_store_2069.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.connectors.gedimat.public_client import (
    GedimatPublicClient,
    fetch_price_cards,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sonde catalogue public Gedimat")
    parser.add_argument("--store-id", type=int, default=2069)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()

    client = GedimatPublicClient.from_public_page()
    store_id = args.store_id
    print(
        json.dumps(
            {
                "application_id": client.config.application_id,
                "index": client.config.index_name,
                "valid_until": client.config.valid_until,
                "api_key_embedded": False,
            }
        )
    )
    for query in ("", "%"):
        result = client.search(
            store_id=store_id,
            query=query,
            hits_per_page=1,
            attributes=("objectID",),
        )
        print(
            json.dumps(
                {
                    "query": query,
                    "http": result.get("_http_status"),
                    "nbHits": result.get("nbHits"),
                    "nbPages": result.get("nbPages"),
                    "hitsPerPage": result.get("hitsPerPage"),
                    "received": len(result.get("hits") or []),
                }
            )
        )
    capped = client.search(
        store_id=store_id,
        hits_per_page=1000,
        page=1,
        attributes=("objectID",),
    )
    print(
        json.dumps(
            {
                "page1_hitsPerPage_1000": len(capped.get("hits") or []),
                "message": capped.get("message"),
            }
        )
    )
    if not args.skip_collect:
        collected = client.collect_store(store_id)
        products = collected.pop("products")
        announced = client.search(
            store_id=store_id, hits_per_page=0, attributes=()
        )
        print(
            json.dumps(
                {
                    "announced_nbHits": announced.get("nbHits"),
                    "exhaustive_nbHits": announced.get("exhaustiveNbHits"),
                    "facet_unique": collected["facet_unique"],
                    "id_ranges": collected["id_ranges"],
                    "id_partition_fetched": collected["id_partition_fetched"],
                    "unique_objectID": collected["unique"],
                    "unique_tellus_variant": collected["unique_tellus_variant"],
                    "fetched": collected["fetched"],
                    "duplicates": collected["duplicates"],
                    "segments": collected["segments"],
                    "facet_values": collected["facet_values"],
                }
            )
        )
        if args.out is not None:
            sample = next(iter(products.values()), {})
            args.out.write_text(
                json.dumps(
                    {
                        "store_id": store_id,
                        "unique": collected["unique"],
                        "products": list(products.values()),
                    },
                    ensure_ascii=False,
                )
            )
            print(json.dumps({"saved": str(args.out), "sample_keys": sorted(sample)}))
    price_status, price_body = fetch_price_cards([1026986])
    first = next(iter(price_body.values()), {}) if isinstance(price_body, dict) else {}
    html = first.get("html") if isinstance(first, dict) else ""
    print(
        json.dumps(
            {
                "prix_endpoint": "POST /produits/prix",
                "prix_root_status_note": "/prix n'est pas l'endpoint du listing",
                "status": price_status,
                "keys": list(price_body)[:3] if isinstance(price_body, dict) else [],
                "html_has_price_markup": "€" in (html or "") or "TTC" in (html or ""),
                "html_excerpt": " ".join((html or "")[:180].split()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
