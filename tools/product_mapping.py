#!/usr/bin/env python3
"""Mapping produit ProMatConnect — PLAQUE_PLATRE / OSSATURE_PLACO.

Par défaut : analyse dry-run, AUCUNE écriture de SupplierProduct.product_id.

Exemples :
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all
  python tools/product_mapping.py --supplier BRICO_DEPOT --category OSSATURE_PLACO --all
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all --apply-exact
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
from app.models.product_mapping import (
    CATEGORY_OSSATURE_PLACO,
    CATEGORY_PLAQUE_PLATRE,
)
from app.services.product_mapping import ProductMappingService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--supplier", default="BRICO_DEPOT")
    p.add_argument(
        "--category",
        default=CATEGORY_PLAQUE_PLATRE,
        choices=(CATEGORY_PLAQUE_PLATRE, CATEGORY_OSSATURE_PLACO),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max éléments classifiés (ignoré si --all).",
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
        help="Persister features/proposals (jamais product_id). PLAQUE uniquement.",
    )
    p.add_argument(
        "--apply-exact",
        action="store_true",
        help=(
            "Appliquer les mappings EXACT (PLAQUE_PLATRE uniquement en V1). "
            "Sans cette option : aucune écriture product_id."
        ),
    )
    p.add_argument(
        "--diagnose",
        action="store_true",
        help="Lister REVIEW/INSUFFICIENT/NO_PMC (PLAQUE_PLATRE).",
    )
    p.add_argument(
        "--example-limit",
        type=int,
        default=10,
        help="Nombre max d'exemples dans le rapport (défaut 10).",
    )
    return p.parse_args(argv)


def _print_plaque_report(result) -> None:
    d = result.to_dict()
    print("=== Mapping PLAQUE_PLATRE ===")
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
    apps = d.get("exact_applications") or []
    print(f"\n--- Exact applicables : {len(apps)} ---")
    for a in apps:
        print(
            f"  SP {a['supplier_product_id']} | {a['supplier_reference']} | "
            f"{a['product_code']}"
        )
    print()
    print("--- JSON ---")
    print(json.dumps(d, ensure_ascii=False, indent=2))


def _print_ossature_report(result) -> None:
    d = result.to_dict()
    print("=== Mapping OSSATURE_PLACO (V1 dry-run) ===")
    print(f"1. SupplierProduct total        : {d['supplier_product_total']}")
    print(f"2. Candidats initiaux (SQL)     : {d['initial_candidates']}")
    print(f"3. NOT_THIS_CATEGORY            : {d['not_this_category']}")
    print(f"4. Vrais éléments               : {d['true_elements']}")
    print(f"   RAIL                         : {d['rails']}")
    print(f"   MONTANT                      : {d['montants']}")
    print(f"   FOURRURE                     : {d['fourrures']}")
    print(f"5. ALREADY_MAPPED               : {d['already_mapped']}")
    print(f"6. EXACT                        : {d['exact']}")
    print(f"7. NO_PMC_PRODUCT               : {d['no_pmc_product']}")
    print(f"8. INSUFFICIENT_DATA            : {d['insufficient_data']}")
    print(f"9. AMBIGUOUS                    : {d['ambiguous']}")
    print(f"10. REVIEW                      : {d['review']}")
    print()
    print("--- NO_PMC_PRODUCT par variante ---")
    missing = d["missing_pmc_by_variant"]
    if not missing:
        print("(aucun)")
    else:
        for label, n in missing.items():
            print(f"  {label} : {n}")
    print()
    print("--- Longueurs proches (réel → nominal) ---")
    for c in d["near_length_cases"]:
        print(
            f"  SP {c['id']} | {c['supplier_reference']} | mapped={c.get('product_id')} "
            f"| {c['kind']} {c['profile']} réel={c['length_mm']} "
            f"nominal={c.get('nominal_length_mm')} | {c['pair']}"
        )
        print(f"    {c['designation']}")
    print()
    print("--- INSUFFICIENT_DATA ---")
    for c in d.get("insufficient_cases") or []:
        print(
            f"  SP {c['id']} | {c['supplier_reference']} | missing={c['missing']} "
            f"| {c['group']} | {c.get('hint')}"
        )
        print(f"    kind={c['kind']} profile={c['profile']} L={c['length_mm']}")
        print(f"    {c['designation']}")
    print()
    print("--- Faux positifs (échantillons) ---")
    for s in d["false_positive_samples"][:12]:
        print(f"  - {s}")
    print()
    print("--- Exemples ---")
    for ex in result.examples[:10]:
        print("\n---")
        print(f"ref: {ex.supplier_reference}")
        print(f"designation: {ex.designation}")
        print(f"extracted: {ex.extracted}")
        print(f"candidate: {ex.candidate_code}")
        print(f"status: {ex.status}  reason: {ex.reason}")
    print()
    print("--- JSON ---")
    print(json.dumps(d, ensure_ascii=False, indent=2))


def _print_apply(result) -> None:
    d = result.to_dict()
    print("=== Apply EXACT PLAQUE_PLATRE ===")
    print(f"dry_run={d['dry_run']} analysed={d['analysed']} "
          f"already_mapped={d['already_mapped']} exact_candidates={d['exact_candidates']} "
          f"applied={d['applied']} skipped={d['skipped']} errors={d['errors']}")
    print(json.dumps(d, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apply_exact and args.category != CATEGORY_PLAQUE_PLATRE:
        print(
            "Refusé : --apply-exact n'est disponible que pour PLAQUE_PLATRE.",
            file=sys.stderr,
        )
        return 2
    if args.apply_exact and args.diagnose:
        print("Refusé : --apply-exact et --diagnose sont exclusifs.", file=sys.stderr)
        return 2
    if not args.all_candidates and not args.diagnose and not args.apply_exact and (
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
            if args.category != CATEGORY_PLAQUE_PLATRE:
                print("Diagnose : PLAQUE_PLATRE uniquement.", file=sys.stderr)
                return 2
            print(json.dumps(svc.diagnose_unresolved(supplier_name=args.supplier),
                             ensure_ascii=False, indent=2))
            return 0

        if args.apply_exact:
            try:
                apply_result = svc.apply_exact_plaque_platre(
                    supplier_name=args.supplier, dry_run=False
                )
                session.commit()
            except Exception as exc:
                session.rollback()
                print(f"ERREUR apply-exact (rollback) : {exc}", file=sys.stderr)
                return 1
            _print_apply(apply_result)
            return 0

        if args.category == CATEGORY_OSSATURE_PLACO:
            result = svc.run_ossature_placo(
                supplier_name=args.supplier,
                limit=None if args.all_candidates else args.limit,
                all_candidates=args.all_candidates,
                example_limit=min(args.example_limit, 10),
            )
            _print_ossature_report(result)
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
        _print_plaque_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
