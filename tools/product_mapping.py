#!/usr/bin/env python3
"""Mapping produit ProMatConnect — dry-run V1 PLAQUE_PLATRE.

Ne modifie JAMAIS SupplierProduct.product_id.
Par défaut : dry-run sans persistance.

Exemple :
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --limit 50 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import make_engine
from app.models.product_mapping import CATEGORY_PLAQUE_PLATRE
from app.services.product_mapping import ProductMappingService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--supplier", default="BRICO_DEPOT")
    p.add_argument("--category", default=CATEGORY_PLAQUE_PLATRE)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument(
        "--persist",
        action="store_true",
        help="Persister features/proposals (jamais product_id). Désactive dry-run mémoire.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.category != CATEGORY_PLAQUE_PLATRE:
        print(
            f"V1 : seule la catégorie {CATEGORY_PLAQUE_PLATRE} est supportée "
            f"(reçu {args.category}).",
            file=sys.stderr,
        )
        return 2
    if args.limit < 1 or args.limit > 200:
        print("Refusé : --limit entre 1 et 200 pour V1.", file=sys.stderr)
        return 2

    dry_run = not args.persist
    settings = Settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        # create_all additif pour nouvelles tables mapping uniquement
        from app.database import Base

        Base.metadata.create_all(
            engine,
            tables=[
                Base.metadata.tables[name]
                for name in (
                    "product_categories",
                    "product_attribute_defs",
                    "supplier_product_features",
                    "product_mapping_proposals",
                )
                if name in Base.metadata.tables
            ],
        )
        svc = ProductMappingService(session)
        result = svc.run_plaque_platre(
            supplier_name=args.supplier,
            limit=args.limit,
            dry_run=dry_run,
            persist=args.persist,
            example_limit=5,
        )
        if args.persist:
            session.commit()

        print("=== Mapping PLAQUE_PLATRE (V1) ===")
        print(
            f"analysed={result.analysed} classified={result.classified} "
            f"exact={result.exact} high={result.high} "
            f"review={result.review} unmapped={result.unmapped}"
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        for ex in result.examples:
            print("\n---")
            print(f"SupplierProduct: {ex.designation}")
            print(
                f"extracted: {ex.extracted.get('type')} / "
                f"{ex.extracted.get('length_mm')} / "
                f"{ex.extracted.get('width_mm')} / "
                f"{ex.extracted.get('thickness_mm')} "
                f"(hydro={ex.extracted.get('hydrofuge')})"
            )
            print(f"candidate: {ex.candidate_code}")
            print(f"status: {ex.status}")
            print(f"score: {ex.score}")
            print(f"existing_product_id: {ex.existing_product_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
