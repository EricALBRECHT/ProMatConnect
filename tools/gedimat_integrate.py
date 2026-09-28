"""Import catalogue Gedimat + mapping PMC + sonde cache live. Pas de commit git.

Usage :
  python tools/gedimat_integrate.py --json /tmp/gedimat_store_2069.json --store-id 2069
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.connectors.gedimat.connector import CACHE_CONNECTOR_KEY, GedimatConnector
from app.database import make_engine
from app.models import Supplier, SupplierProduct
from app.services.gedimat_catalog_import import import_gedimat_catalog, load_catalog_hits
from app.services.gedimat_mapping import gedimat_coverage, map_gedimat_catalog
from app.services.supplier_live_cache import SupplierLiveCacheService


def main() -> None:
    parser = argparse.ArgumentParser(description="Intégration catalogue Gedimat")
    parser.add_argument("--json", type=Path, default=Path("/tmp/gedimat_store_2069.json"))
    parser.add_argument("--store-id", type=int, default=2069)
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    engine = make_engine(settings.database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    hits = load_catalog_hits(args.json)

    with factory() as session:
        brico_before = _brico_counts(session)
        print("import 1", flush=True)
        first_import = import_gedimat_catalog(session, hits)
        session.commit()
        print("map 1", flush=True)
        first_map = map_gedimat_catalog(session)
        session.commit()
        print("import 2", flush=True)
        second_import = import_gedimat_catalog(session, hits)
        session.commit()
        print("map 2", flush=True)
        second_map = map_gedimat_catalog(session)
        session.commit()
        coverage = gedimat_coverage(session)
        brico_after = _brico_counts(session)
        test_supplier = _count_name(session, "GEDIMAT TEST")
        live = None
        if not args.skip_live:
            live = _live_probe(session, settings, store_id=args.store_id)
        print(
            json.dumps(
                {
                    "import_1": _import_dict(first_import),
                    "map_1": first_map,
                    "import_2": _import_dict(second_import),
                    "map_2": second_map,
                    "coverage": coverage,
                    "brico_before": brico_before,
                    "brico_after": brico_after,
                    "gedimat_test": test_supplier,
                    "live": live,
                },
                ensure_ascii=False,
                default=str,
            )
        )


def _import_dict(result) -> dict:
    return {
        "created": result.created,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "deactivated": result.deactivated,
        "variants": result.variants,
        "collapsed_object_ids": result.collapsed_object_ids,
        "errors": result.errors,
    }


def _brico_counts(session) -> dict:
    row = session.execute(
        text(
            """
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE sp.product_id IS NOT NULL) AS mapped,
              count(*) FILTER (WHERE sp.product_id IS NULL) AS unmapped
            FROM supplier_products sp
            JOIN suppliers s ON s.id = sp.supplier_id
            WHERE s.name = 'BRICO_DEPOT'
            """
        )
    ).one()
    return {"total": row.total, "mapped": row.mapped, "unmapped": row.unmapped}


def _count_name(session, name: str) -> int:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == name))
    if supplier_id is None:
        return 0
    return int(
        session.scalar(
            select(func.count()).select_from(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier_id
            )
        )
        or 0
    )


def _live_probe(session, settings: Settings, *, store_id: int) -> dict:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == "GEDIMAT"))
    sp = session.scalar(
        select(SupplierProduct)
        .where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.product_id.is_not(None),
            SupplierProduct.active.is_(True),
        )
        .order_by(SupplierProduct.id)
        .limit(1)
    )
    if sp is None or sp.product_id is None:
        return {"ok": False, "reason": "aucun produit mappé"}
    cache = SupplierLiveCacheService(session, settings)
    store_key = str(store_id)
    existing = cache.get_offer(CACHE_CONNECTOR_KEY, store_key, sp.supplier_reference, allow_stale=True)
    if existing is not None:
        row = cache._get_offer_row(CACHE_CONNECTOR_KEY, store_key, sp.supplier_reference)
        if row is not None:
            session.delete(row)
            session.commit()
    connector = GedimatConnector(session, store_id=store_id, cache=cache, settings=settings)
    before = connector.client.http_calls
    first = connector.get_offers([sp.product_id])
    after_miss = connector.client.http_calls
    second = connector.get_offers([sp.product_id])
    after_hit = connector.client.http_calls
    third = connector.get_offers([sp.product_id], force_refresh=True)
    after_refresh = connector.client.http_calls
    offer = first[0] if first else None
    return {
        "sku": sp.supplier_reference,
        "product_id": sp.product_id,
        "calls_before": before,
        "calls_after_miss": after_miss,
        "calls_after_hit": after_hit,
        "calls_after_refresh": after_refresh,
        "miss": after_miss == before + 1,
        "hit": after_hit == after_miss and len(second) == len(first),
        "refresh": after_refresh == after_hit + 1,
        "price": str(offer.price) if offer is not None else None,
        "fulfillment": offer.fulfillment if offer is not None else None,
        "offers": len(first),
    }


if __name__ == "__main__":
    main()
