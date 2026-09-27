#!/usr/bin/env python3
"""Mapping produit ProMatConnect — analyse V1.3 PLAQUE_PLATRE.

Ne modifie JAMAIS SupplierProduct.product_id.
Par défaut : dry-run sans persistance.

Exemples :
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all --dry-run
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all --diagnose
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
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max vraies plaques analysées (ignoré si --all).",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="all_candidates",
        help="Toutes les références candidates SQL de la famille.",
    )
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument(
        "--persist",
        action="store_true",
        help="Persister features/proposals (jamais product_id).",
    )
    p.add_argument(
        "--diagnose",
        action="store_true",
        help="Lister REVIEW/INSUFFICIENT/NO_PMC avec raisons explicites.",
    )
    p.add_argument(
        "--example-limit",
        type=int,
        default=10,
        help="Nombre max d'exemples dans le rapport (défaut 10).",
    )
    return p.parse_args(argv)


def _print_report(result) -> None:
    d = result.to_dict()
    print("=== Mapping PLAQUE_PLATRE (V1.3) ===")
    print(f"1. SupplierProduct total        : {d['supplier_product_total']}")
    print(f"2. Candidats initiaux (SQL)     : {d['initial_candidates']}")
    print(f"3. NOT_THIS_CATEGORY            : {d['not_this_category']}")
    print(f"4. Vraies plaques retenues      : {d['true_plaques']}")
    print(f"5. ALREADY_MAPPED               : {d['already_mapped']}")
    print(f"6. EXACT                        : {d['exact']}")
    print(f"7. HIGH                         : {d['high']}")
    print(f"8. REVIEW                       : {d['review']}")
    print(f"9. NO_PMC_PRODUCT               : {d['no_pmc_product']}")
    print(f"10. INSUFFICIENT_DATA           : {d['insufficient_data']}")
    print(f"11. AMBIGUOUS                   : {d['ambiguous']}")
    print()
    print("--- Variantes / dimensions ---")
    variants = d["variants"]
    print(f"par type        : {variants['by_type']}")
    print(f"par épaisseur   : {variants['by_thickness_mm']}")
    print(f"par longueur    : {variants['by_length_mm']}")
    print(f"par largeur     : {variants['by_width_mm']}")
    print(f"combinaisons    : {variants['by_dims']}")
    print(f"variantes       : {variants['by_variant']}")
    print(f"flags           : {variants['flags']}")
    if variants["other_types"]:
        print(f"autres types    : {variants['other_types']}")
    print()
    print("--- Product PMC manquants (NO_PMC_PRODUCT) ---")
    missing = d["missing_pmc_by_variant"]
    if not missing:
        print("(aucun)")
    else:
        for label, n in missing.items():
            print(f"  {label} : {n} références fournisseur")
    print()
    print(f"category_path disponible : {d['category_path_available']}")
    print(f"note : {d['category_path_note']}")
    print()
    print("--- Exemples ---")
    for ex in result.examples[:10]:
        print("\n---")
        print(f"ref: {ex.supplier_reference}")
        print(f"designation: {ex.designation}")
        print(f"extracted: {ex.extracted}")
        print(f"candidate: {ex.candidate_code}")
        print(f"status: {ex.status}  reason: {ex.reason}")
        print(f"score: {ex.score}  existing_product_id: {ex.existing_product_id}")
    print()
    print("--- JSON ---")
    print(json.dumps(d, ensure_ascii=False, indent=2))


def _print_diagnose(diag: dict) -> None:
    print("=== Diagnostic PLAQUE_PLATRE (non-EXACT) ===")
    print(f"population: {diag['population']}")
    print(f"gap_counts: {diag['gap_counts']}")
    for c in diag["cases"]:
        print("\n---")
        print(f"id={c['id']} ref={c['supplier_reference']}")
        print(f"designation: {c['designation']}")
        print(
            f"extracted: type={c['type']} {c['length_mm']}×{c['width_mm']}×{c['thickness_mm']} "
            f"hydro={c['hydrofuge']} feu={c['fire_resistant']} acou={c['acoustic']}"
        )
        print(f"reason={c['match_reason']} status={c['match_status']} candidate={c['candidate']}")
        print(f"gaps: {c['gaps']}")
        if c["pmc_same_dims"]:
            print(f"pmc_same_dims: {[p['code'] for p in c['pmc_same_dims']]}")
    print()
    print("--- JSON ---")
    print(json.dumps(diag, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.category != CATEGORY_PLAQUE_PLATRE:
        print(
            f"V1.3 : seule la catégorie {CATEGORY_PLAQUE_PLATRE} est supportée "
            f"(reçu {args.category}).",
            file=sys.stderr,
        )
        return 2
    if not args.all_candidates and not args.diagnose and (
        args.limit < 1 or args.limit > 5000
    ):
        print("Refusé : --limit entre 1 et 5000 (ou utiliser --all).", file=sys.stderr)
        return 2

    dry_run = not args.persist
    settings = Settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
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
        if args.diagnose:
            diag = svc.diagnose_unresolved(supplier_name=args.supplier)
            _print_diagnose(diag)
            return 0

        result = svc.run_plaque_platre(
            supplier_name=args.supplier,
            limit=None if args.all_candidates else args.limit,
            all_candidates=args.all_candidates,
            dry_run=dry_run,
            persist=args.persist,
            example_limit=min(args.example_limit, 10),
        )
        if args.persist:
            session.commit()

        _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
