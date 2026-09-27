#!/usr/bin/env python3
"""Mapping produit ProMatConnect — analyse / apply EXACT PLAQUE_PLATRE V1.5.

Par défaut : analyse dry-run, AUCUNE écriture de SupplierProduct.product_id.

Exemples :
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all --diagnose
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
        "--apply-exact",
        action="store_true",
        help=(
            "Appliquer les mappings EXACT éligibles "
            "(product_id + correction_source=exact_rule). "
            "Sans cette option : aucune écriture product_id."
        ),
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
    print("=== Mapping PLAQUE_PLATRE (V1.5) ===")
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
    apps = d.get("exact_applications") or []
    print(f"--- Exact applicables (product_id NULL) : {len(apps)} ---")
    for a in apps:
        print(
            f"  SP {a['supplier_product_id']} | {a['supplier_reference']} | "
            f"{a['product_code']} | {a['designation'][:80]}"
        )
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


def _print_apply(result) -> None:
    d = result.to_dict()
    print("=== Apply EXACT PLAQUE_PLATRE (V1.5) ===")
    print(f"dry_run            : {d['dry_run']}")
    print(f"analysed           : {d['analysed']}")
    print(f"already_mapped     : {d['already_mapped']}")
    print(f"exact_candidates   : {d['exact_candidates']}")
    print(f"applied            : {d['applied']}")
    print(f"skipped            : {d['skipped']}")
    print(f"errors             : {d['errors']}")
    print()
    print("--- Mappings ---")
    for a in d["applications"]:
        print(
            f"  SP {a['supplier_product_id']} | {a['supplier_reference']} | "
            f"{a['product_code']} | {a['designation'][:80]}"
        )
    if d["error_messages"]:
        print("--- Errors ---")
        for msg in d["error_messages"]:
            print(f"  {msg}")
    print()
    print("--- JSON ---")
    print(json.dumps(d, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.category != CATEGORY_PLAQUE_PLATRE:
        print(
            f"V1.5 : seule la catégorie {CATEGORY_PLAQUE_PLATRE} est supportée "
            f"(reçu {args.category}).",
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
            diag = svc.diagnose_unresolved(supplier_name=args.supplier)
            _print_diagnose(diag)
            return 0

        if args.apply_exact:
            try:
                apply_result = svc.apply_exact_plaque_platre(
                    supplier_name=args.supplier,
                    dry_run=False,
                )
                session.commit()
            except Exception as exc:
                session.rollback()
                print(f"ERREUR apply-exact (rollback) : {exc}", file=sys.stderr)
                return 1
            _print_apply(apply_result)
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
