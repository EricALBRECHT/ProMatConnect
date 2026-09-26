#!/usr/bin/env python3
"""Probe contrôlé Brico LIVE + cache (réseau manuel).

Usage :
  python tools/bricodepot_live_compare_probe.py --insee 80021 \\
    --sku 3334160524579 --sku 3596265336819

Vérifie :
  - 1er passage : store MISS + offres MISS (appels HTTP)
  - 2e passage : HIT cache (pas de nouvel appel produit si frais)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.config import Settings  # noqa: E402
from app.connectors.bricodepot.client import BricoDepotClient  # noqa: E402
from app.connectors.bricodepot.connector import CACHE_CONNECTOR_KEY  # noqa: E402
from app.database import Base, make_engine  # noqa: E402
from app.services.supplier_live_cache import SupplierLiveCacheService  # noqa: E402


class CountingTransport:
    def __init__(self):
        from app.connectors.bricodepot.client import default_transport

        self._inner = default_transport
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return self._inner(**kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Brico LIVE + cache Amiens")
    parser.add_argument("--insee", default="80021")
    parser.add_argument("--sku", action="append", dest="skus", default=[])
    parser.add_argument(
        "--database-url",
        default=None,
        help="Défaut : SQLite temporaire (pas besoin de Postgres).",
    )
    args = parser.parse_args(argv)
    skus = args.skus or ["3334160524579", "3596265336819"]

    if args.database_url:
        database_url = args.database_url
        cleanup_db = None
    else:
        tmp = tempfile.NamedTemporaryFile(prefix="brico_probe_", suffix=".db", delete=False)
        tmp.close()
        cleanup_db = Path(tmp.name)
        database_url = f"sqlite:///{cleanup_db}"

    settings = Settings(
        bricodepot_live_enabled=True,
        database_url=database_url,
    )
    engine = make_engine(settings.database_url)
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)

    transport = CountingTransport()
    client = BricoDepotClient(transport=transport)

    try:
        with Session(engine) as session:
            cache = SupplierLiveCacheService(session, settings)
            print(f"INSEE={args.insee} SKUs={skus}")
            print("--- Passage 1 (MISS attendu) ---")
            c1 = transport.calls
            retailers = client.fetch_retailers(args.insee)
            print(f"retailers={len(retailers)} http_calls={transport.calls - c1}")
            if not retailers:
                print("Aucun magasin — arrêt")
                return 1
            r0 = retailers[0]
            print(
                f"first entity_id={r0.entity_id} seller_code={r0.seller_code} "
                f"agency_key=bricodepot:{r0.entity_id}"
            )
            cache.put_stores(
                CACHE_CONNECTOR_KEY,
                args.insee,
                [
                    {
                        "entity_id": r.entity_id,
                        "seller_code": r.seller_code,
                        "name": r.name,
                        "address": r.address,
                        "city": r.city,
                        "postcode": r.postcode,
                        "latitude": r.latitude,
                        "longitude": r.longitude,
                        "phone": r.phone,
                        "distance_km": r.distance_km,
                    }
                    for r in retailers
                ],
            )
            by_sku = client.fetch_products_by_sku(seller_id=r0.entity_id, skus=skus)
            for sku in skus:
                offer = by_sku.get(sku)
                if offer is None or offer.price_ht_piece.value is None:
                    print(f"SKU {sku}: ABSENT ou HT null")
                    continue
                cache.put_offer(
                    CACHE_CONNECTOR_KEY,
                    str(r0.entity_id),
                    sku,
                    seller_code=r0.seller_code,
                    price_ht=offer.price_ht_piece.value,
                    price_ttc=offer.price_ttc_piece.value,
                    stock_quantity=offer.stock_quantity,
                    stock_status=offer.stock_status,
                    is_salable=offer.is_salable,
                    is_offer_available=offer.is_offer_available,
                )
                print(
                    f"SKU {sku}: HT={offer.price_ht_piece.value} "
                    f"TTC={offer.price_ttc_piece.value} stock={offer.stock_quantity}"
                )
            session.commit()

            print(f"http_calls total après passage 1: {transport.calls}")
            print("--- Passage 2 (HIT cache attendu) ---")
            c2 = transport.calls
            hit = cache.get_stores(CACHE_CONNECTOR_KEY, args.insee)
            print(f"store_cache_fresh={hit is not None and hit.is_fresh}")
            for sku in skus:
                entry = cache.get_offer(CACHE_CONNECTOR_KEY, str(r0.entity_id), sku)
                print(
                    f"offer {sku}: fresh_price={getattr(entry, 'price_fresh', None)} "
                    f"fresh_stock={getattr(entry, 'stock_fresh', None)} "
                    f"ht={getattr(entry, 'price_ht', None)}"
                )
            print(f"http_calls pendant passage 2: {transport.calls - c2} (attendu 0)")
            return 0
    finally:
        engine.dispose()
        if cleanup_db is not None:
            cleanup_db.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
