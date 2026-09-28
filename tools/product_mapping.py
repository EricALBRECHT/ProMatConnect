#!/usr/bin/env python3
"""Mapping produit ProMatConnect — PLAQUE_PLATRE / OSSATURE_PLACO / VIS_PLACO / VIS_AGGLO.

Par défaut : analyse dry-run, AUCUNE écriture de SupplierProduct.product_id.

Exemples :
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all
  python tools/product_mapping.py --supplier BRICO_DEPOT --category OSSATURE_PLACO --all
  python tools/product_mapping.py --supplier BRICO_DEPOT --category VIS_PLACO --all
  python tools/product_mapping.py --supplier BRICO_DEPOT --category VIS_AGGLO --all
  python tools/product_mapping.py --supplier BRICO_DEPOT --category PLAQUE_PLATRE --all --apply-exact
  python tools/product_mapping.py catalog-discover --supplier BRICO_DEPOT --format text
  python tools/product_mapping.py catalog-discover --format json
  python tools/product_mapping.py catalog-discover --discover-version 1   # V1 legacy
  python tools/product_mapping.py catalog-batch-discover --supplier BRICO_DEPOT --format text
  python tools/product_mapping.py catalog-batch-discover --format json --min-size 15 --top 50
  python tools/product_mapping.py catalog-batch-consolidate --supplier BRICO_DEPOT --format text
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import make_engine
from app.models.product_mapping import (
    CATEGORY_CHEVILLE_METAL,
    CATEGORY_OSSATURE_PLACO,
    CATEGORY_PLAQUE_PLATRE,
    CATEGORY_VIS_AGGLO,
    CATEGORY_VIS_BOIS,
    CATEGORY_VIS_MULTI,
    CATEGORY_VIS_PLACO,
)
from app.services.product_mapping import ProductMappingService
from app.services.product_mapping.catalog_discover import (
    CLI_DISCOVER_VERSION,
    DEFAULT_EXAMPLE_LIMIT,
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_RECLUSTER_THRESHOLD,
    DEFAULT_TOP_ACTIONABLE,
    DEFAULT_TOP_CLUSTERS,
    format_text_report,
    run_catalog_discover,
)
from app.services.product_mapping.batch_discover import (
    DEFAULT_MIN_SIZE as BATCH_DEFAULT_MIN_SIZE,
    DEFAULT_TOP as BATCH_DEFAULT_TOP,
    format_text_report as format_batch_text_report,
    run_batch_discover,
)
from app.services.product_mapping.batch_consolidate import (
    format_consolidation_text,
    run_batch_consolidate,
)
from app.services.product_mapping.fastener_batch import (
    apply_if_clean,
    audit_exact_matches,
    count_mapped_sp,
    dry_run_summary,
    seed_fastener_family_products,
)
from app.services.product_mapping import profiles_batch as profiles_batch_svc
from app.services.product_mapping.generic_matcher import (
    REASON_ALREADY_MAPPED,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
)
from app.services.product_mapping.pipeline import (
    OUTCOME_ALREADY_MAPPED,
    OUTCOME_NOT_THIS_CATEGORY,
)
from app.services.product_mapping.rules import get_rule


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--supplier", default="BRICO_DEPOT")
    p.add_argument(
        "--category",
        default=CATEGORY_PLAQUE_PLATRE,
        choices=(
            CATEGORY_PLAQUE_PLATRE,
            CATEGORY_OSSATURE_PLACO,
            CATEGORY_VIS_PLACO,
            CATEGORY_VIS_AGGLO,
        ),
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


def _identity_label(rule, attrs: dict) -> str:
    """Clé d'identité lisible — « GROUP BY identité » du rapport générique."""
    return " | ".join(
        f"{key}={attrs.get(key) if attrs.get(key) is not None else '?'}"
        for key in rule.identity_keys
    )


def _proposed_pmc_code(rule, attrs: dict) -> str | None:
    """Code PMC proposé : diamètre sans virgule × longueur (35X45 = 3,5 × 45 mm)."""
    if not rule.pmc_code_prefixes:
        return None
    diameter = attrs.get("diameter_mm")
    length = attrs.get("length_mm")
    if diameter is None or length is None:
        return None
    tenths = int(round(float(diameter) * 10))
    return f"{rule.pmc_code_prefixes[0]}{tenths}X{int(length)}"


def _print_generic_report(result, rule) -> None:
    """Rapport générique CategoryRunResult — compteurs, variantes, manques PMC."""
    print(f"=== Mapping {rule.code} (V2 générique, dry-run) ===")
    print(f"1. SupplierProduct total        : {result.supplier_product_total}")
    print(f"2. Candidats initiaux (SQL)     : {result.initial_candidates}")
    print(f"3. Analysés                     : {result.analysed}")
    print(f"4. NOT_THIS_CATEGORY            : {result.not_this_category}")
    print(f"5. Classifiés                   : {result.classified}")
    print(f"6. ALREADY_MAPPED               : {result.already_mapped}")
    print(f"7. EXACT                        : {result.exact}")
    print(f"8. HIGH                         : {result.high}")
    print(f"9. REVIEW                       : {result.review}")
    print(f"10. AMBIGUOUS                   : {result.ambiguous}")
    print(f"11. NO_PMC_PRODUCT              : {result.no_pmc_product}")
    print(f"12. INSUFFICIENT_DATA           : {result.insufficient_data}")
    print(f"13. Candidats PMC chargés       : {len(result.pmc_candidates)}")

    variants: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    proposals: dict[str, str] = {}
    insufficient: list[tuple] = []
    false_positives: list[str] = []

    for item in result.items:
        if item.outcome == OUTCOME_NOT_THIS_CATEGORY:
            if len(false_positives) < 15:
                false_positives.append((item.sp.designation or "")[:120])
            continue
        label = _identity_label(rule, item.attrs)
        variants[label] += 1
        if item.reason == REASON_NO_PMC_PRODUCT:
            missing[label] += 1
            code = _proposed_pmc_code(rule, item.attrs)
            if code:
                proposals[label] = code
        elif item.reason == REASON_INSUFFICIENT_DATA and len(insufficient) < 20:
            insufficient.append(
                (item.sp.id, item.sp.supplier_reference, item.sp.designation, item.attrs)
            )

    print(f"\n--- Variantes classifiées (GROUP BY identité) : {len(variants)} ---")
    for label, n in variants.most_common():
        print(f"  {n:4d}  {label}")

    print(f"\n--- NO_PMC_PRODUCT par identité : {len(missing)} ---")
    for label, n in missing.most_common():
        print(f"  {n:4d}  {label}  → proposer {proposals.get(label, '(identité incomplète)')}")

    print(f"\n--- PMC manquants proposés : {len(set(proposals.values()))} ---")
    for code in sorted(set(proposals.values())):
        print(f"  {code}")

    print(f"\n--- INSUFFICIENT_DATA : {result.insufficient_data} ---")
    for sp_id, ref, designation, attrs in insufficient:
        manquant = [k for k in rule.identity_keys if attrs.get(k) is None]
        print(f"  SP {sp_id} | {ref} | manque={manquant}")
        print(f"    {designation}")

    print("\n--- ALREADY_MAPPED ---")
    for item in result.items:
        if item.reason != REASON_ALREADY_MAPPED:
            continue
        print(
            f"  SP {item.sp.id} | {item.sp.supplier_reference} | "
            f"product_id={item.sp.product_id} | {_identity_label(rule, item.attrs)}"
        )
        print(f"    {(item.sp.designation or '')[:140]}")

    print("\n--- Faux positifs écartés (échantillon) ---")
    for sample in false_positives:
        print(f"  - {sample}")

    print("\n--- Exemples ---")
    shown = 0
    for item in result.items:
        if item.outcome == OUTCOME_NOT_THIS_CATEGORY or shown >= 10:
            continue
        shown += 1
        status = "ALREADY_MAPPED" if item.outcome == OUTCOME_ALREADY_MAPPED else (
            item.match.status.upper() if item.match else "?"
        )
        print("\n---")
        print(f"ref: {item.sp.supplier_reference}")
        print(f"designation: {(item.sp.designation or '')[:160]}")
        print(f"extracted: {item.attrs}")
        print(f"candidate: {item.match.product_code if item.match else None}")
        print(f"status: {status}  reason: {item.reason}")


CATALOG_DISCOVER_COMMAND = "catalog-discover"
CATALOG_BATCH_DISCOVER_COMMAND = "catalog-batch-discover"
CATALOG_BATCH_CONSOLIDATE_COMMAND = "catalog-batch-consolidate"
FASTENER_BATCH_COMMAND = "fastener-batch"
PROFILES_BATCH_COMMAND = "profiles-batch"
MASS_BATCH_COMMAND = "mass-batch"

FASTENER_BATCH_CATEGORIES = (
    CATEGORY_VIS_BOIS,
    CATEGORY_VIS_MULTI,
    CATEGORY_CHEVILLE_METAL,
)


def parse_catalog_discover_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=f"product_mapping.py {CATALOG_DISCOVER_COMMAND}",
        description="Cartographie lecture seule d'un catalogue fournisseur.",
    )
    p.add_argument(
        "--supplier",
        default=None,
        help="Nom du fournisseur (défaut : tous les fournisseurs).",
    )
    p.add_argument("--format", default="text", choices=("text", "json"))
    p.add_argument(
        "--min-cluster-size",
        type=int,
        default=DEFAULT_MIN_CLUSTER_SIZE,
        help=f"Taille minimale d'un cluster (défaut {DEFAULT_MIN_CLUSTER_SIZE}).",
    )
    p.add_argument("--top-clusters", type=int, default=DEFAULT_TOP_CLUSTERS)
    p.add_argument("--example-limit", type=int, default=DEFAULT_EXAMPLE_LIMIT)
    p.add_argument(
        "--discover-version",
        type=int,
        default=CLI_DISCOVER_VERSION,
        choices=(1, 2),
        help=(
            "1 = clusters par token dominant (V1), 2 = bigrammes discriminants "
            f"+ hiérarchie (défaut {CLI_DISCOVER_VERSION})."
        ),
    )
    p.add_argument(
        "--min-bigram-size",
        type=int,
        default=None,
        help="Seuil des graines bigrammes en V2 (défaut : 60 %% de min-cluster-size).",
    )
    p.add_argument(
        "--recluster-threshold",
        type=int,
        default=DEFAULT_RECLUSTER_THRESHOLD,
        help=(
            "Taille à partir de laquelle un cluster V2 est redécoupé "
            f"(défaut {DEFAULT_RECLUSTER_THRESHOLD})."
        ),
    )
    p.add_argument("--top-actionable", type=int, default=DEFAULT_TOP_ACTIONABLE)
    p.add_argument(
        "--compare-v1",
        action="store_true",
        default=True,
        help="Inclure la transformation des clusters V1 (défaut : activé).",
    )
    p.add_argument(
        "--no-compare-v1",
        action="store_false",
        dest="compare_v1",
        help="Ne pas exécuter la passe de comparaison V1.",
    )
    return p.parse_args(argv)


def main_catalog_discover(argv: list[str]) -> int:
    """Sous-commande lecture seule — aucune écriture, aucune migration."""
    args = parse_catalog_discover_args(argv)
    if args.min_cluster_size < 1:
        print("Refusé : --min-cluster-size >= 1.", file=sys.stderr)
        return 2
    if args.top_clusters < 1 or args.example_limit < 1:
        print("Refusé : --top-clusters et --example-limit >= 1.", file=sys.stderr)
        return 2
    if args.top_actionable < 1 or args.recluster_threshold < 2:
        print(
            "Refusé : --top-actionable >= 1 et --recluster-threshold >= 2.",
            file=sys.stderr,
        )
        return 2
    if args.min_bigram_size is not None and args.min_bigram_size < 1:
        print("Refusé : --min-bigram-size >= 1.", file=sys.stderr)
        return 2

    settings = Settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        report = run_catalog_discover(
            session,
            supplier_name=args.supplier,
            min_cluster_size=args.min_cluster_size,
            top_clusters=args.top_clusters,
            example_limit=args.example_limit,
            version=args.discover_version,
            min_bigram_size=args.min_bigram_size,
            recluster_threshold=args.recluster_threshold,
            top_actionable=args.top_actionable,
            compare_v1=args.compare_v1,
        )
        session.rollback()  # analyse pure : rien à valider
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_text_report(report))
    return 0


def parse_catalog_batch_discover_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=f"product_mapping.py {CATALOG_BATCH_DISCOVER_COMMAND}",
        description=(
            "Qualification READ-ONLY multi-familles (Catalog Discover V2 + "
            "IdentityModel candidates). Aucune écriture."
        ),
    )
    p.add_argument(
        "--supplier",
        default=None,
        help="Nom du fournisseur (défaut : tous les fournisseurs).",
    )
    p.add_argument("--format", default="text", choices=("text", "json"))
    p.add_argument(
        "--min-size",
        type=int,
        default=BATCH_DEFAULT_MIN_SIZE,
        help=f"Taille minimale d'une famille analysée (défaut {BATCH_DEFAULT_MIN_SIZE}).",
    )
    p.add_argument(
        "--top",
        type=int,
        default=BATCH_DEFAULT_TOP,
        help=f"Nombre de quick wins / high impact (défaut {BATCH_DEFAULT_TOP}).",
    )
    p.add_argument(
        "--example-limit",
        type=int,
        default=8,
        help="Exemples représentatifs par famille (défaut 8).",
    )
    p.add_argument(
        "--min-cluster-size",
        type=int,
        default=None,
        help="Seuil clustering Discover V2 (défaut : min(15, min-size)).",
    )
    return p.parse_args(argv)


def main_catalog_batch_discover(argv: list[str]) -> int:
    """Sous-commande lecture seule — aucune écriture métier."""
    args = parse_catalog_batch_discover_args(argv)
    if args.min_size < 1 or args.top < 1 or args.example_limit < 1:
        print("Refusé : --min-size, --top et --example-limit >= 1.", file=sys.stderr)
        return 2
    if args.min_cluster_size is not None and args.min_cluster_size < 1:
        print("Refusé : --min-cluster-size >= 1.", file=sys.stderr)
        return 2

    settings = Settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        report = run_batch_discover(
            session,
            supplier_name=args.supplier,
            min_size=args.min_size,
            top=args.top,
            example_limit=args.example_limit,
            min_cluster_size=args.min_cluster_size,
        )
        session.rollback()
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_batch_text_report(report, top=args.top))
    return 0


def parse_catalog_batch_consolidate_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=f"product_mapping.py {CATALOG_BATCH_CONSOLIDATE_COMMAND}",
        description=(
            "Consolidation READ-ONLY V1.1 des candidats industrialisables "
            "(natures PRODUCT_FAMILY / ATTRIBUTE_CLUSTER, dédoublonnage SP)."
        ),
    )
    p.add_argument("--supplier", default=None)
    p.add_argument("--format", default="text", choices=("text", "json"))
    p.add_argument("--min-size", type=int, default=BATCH_DEFAULT_MIN_SIZE)
    p.add_argument("--top", type=int, default=BATCH_DEFAULT_TOP)
    return p.parse_args(argv)


def main_catalog_batch_consolidate(argv: list[str]) -> int:
    args = parse_catalog_batch_consolidate_args(argv)
    if args.min_size < 1 or args.top < 1:
        print("Refusé : --min-size et --top >= 1.", file=sys.stderr)
        return 2
    settings = Settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        report = run_batch_consolidate(
            session,
            supplier_name=args.supplier,
            min_size=args.min_size,
            top=args.top,
        )
        session.rollback()
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_consolidation_text(report))
    return 0


def parse_fastener_batch_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=f"product_mapping.py {FASTENER_BATCH_COMMAND}",
        description=(
            "Batch FASTENER V1 : seed PMC + dry-run + audit EXACT + apply "
            "(VIS_BOIS, VIS_MULTI, CHEVILLE_METAL)."
        ),
    )
    p.add_argument("--supplier", default="BRICO_DEPOT")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Appliquer EXACT si audit mismatch=0 (sinon seed+dry-run seulement).",
    )
    p.add_argument(
        "--dry-run-only",
        action="store_true",
        help="Ne pas seed ni apply — compteurs dry-run uniquement.",
    )
    p.add_argument("--format", default="text", choices=("text", "json"))
    return p.parse_args(argv)


def main_fastener_batch(argv: list[str]) -> int:
    args = parse_fastener_batch_args(argv)
    settings = Settings()
    engine = make_engine(settings.database_url)
    report: dict = {"families": [], "mapped_before": 0, "mapped_after": 0}
    with Session(engine) as session:
        report["mapped_before"] = count_mapped_sp(session)
        for code in FASTENER_BATCH_CATEGORIES:
            family_report: dict = {"category": code}
            if not args.dry_run_only:
                family_report["seed"] = seed_fastener_family_products(session, code)
                session.flush()
            family_report["dry_run"] = dry_run_summary(session, code)
            family_report["exact_audit"] = audit_exact_matches(session, code)
            if args.apply and not args.dry_run_only:
                family_report["apply"] = apply_if_clean(session, code)
                session.flush()
                # Idempotence : second apply
                family_report["apply_second"] = apply_if_clean(session, code)
                family_report["dry_run_after"] = dry_run_summary(session, code)
            report["families"].append(family_report)
        if args.apply and not args.dry_run_only:
            session.commit()
        else:
            if not args.dry_run_only:
                # Seed only — commit products for subsequent apply runs
                session.commit()
            else:
                session.rollback()
        report["mapped_after"] = count_mapped_sp(session)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print("=== FASTENER BATCH V1 ===")
        print(f"mapped_before={report['mapped_before']} mapped_after={report['mapped_after']}")
        for fam in report["families"]:
            print(f"\n--- {fam['category']} ---")
            if "seed" in fam:
                s = fam["seed"]
                print(
                    f"seed identities={s['identities_found']} "
                    f"created={s['created']} skipped={s['skipped_existing']}"
                )
            d = fam["dry_run"]
            print(
                f"dry-run candidates={d['candidates']} NTC={d['not_this_category']} "
                f"ALREADY={d['already_mapped']} EXACT={d['exact']} "
                f"NO_PMC={d['no_pmc_product']} INSUFF={d['insufficient_data']} "
                f"REVIEW={d['review']} AMBIG={d['ambiguous']}"
            )
            a = fam["exact_audit"]
            print(f"exact_audit ok={a['exact_ok']} mismatches={a['mismatch_count']}")
            if fam.get("apply"):
                ap = fam["apply"]
                print(
                    f"apply applied={ap.get('applied')} "
                    f"second={fam.get('apply_second', {}).get('applied')}"
                )
                if fam.get("dry_run_after"):
                    da = fam["dry_run_after"]
                    print(
                        f"after ALREADY={da['already_mapped']} EXACT={da['exact']}"
                    )
    return 0


def parse_profiles_batch_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=f"product_mapping.py {PROFILES_BATCH_COMMAND}",
        description=(
            "Lot profilés/panneaux : CORNIERE_PVC, ROND_ACIER, TUBE_ROND_ACIER, "
            "FER_BETON, PANNEAU_MDF."
        ),
    )
    p.add_argument("--supplier", default="BRICO_DEPOT")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--dry-run-only", action="store_true")
    p.add_argument("--format", default="text", choices=("text", "json"))
    return p.parse_args(argv)


def main_profiles_batch(argv: list[str]) -> int:
    args = parse_profiles_batch_args(argv)
    settings = Settings()
    engine = make_engine(settings.database_url)
    report: dict = {"families": [], "mapped_before": 0, "mapped_after": 0}
    with Session(engine) as session:
        report["mapped_before"] = profiles_batch_svc.count_mapped_sp(session)
        for code in profiles_batch_svc.LOT_CATEGORIES:
            family_report: dict = {"category": code}
            if not args.dry_run_only:
                family_report["seed"] = profiles_batch_svc.seed_family(session, code)
                session.flush()
            family_report["dry_run"] = profiles_batch_svc.dry_run_summary(session, code)
            family_report["exact_audit"] = profiles_batch_svc.audit_exact_matches(
                session, code
            )
            if args.apply and not args.dry_run_only:
                family_report["apply"] = profiles_batch_svc.apply_if_clean(session, code)
                session.flush()
                family_report["apply_second"] = profiles_batch_svc.apply_if_clean(
                    session, code
                )
                family_report["dry_run_after"] = profiles_batch_svc.dry_run_summary(
                    session, code
                )
            report["families"].append(family_report)
        if args.apply and not args.dry_run_only:
            session.commit()
        elif not args.dry_run_only:
            session.commit()
        else:
            session.rollback()
        report["mapped_after"] = profiles_batch_svc.count_mapped_sp(session)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print("=== PROFILES BATCH ===")
        print(
            f"mapped_before={report['mapped_before']} "
            f"mapped_after={report['mapped_after']}"
        )
        for fam in report["families"]:
            print(f"\n--- {fam['category']} ---")
            if "seed" in fam:
                s = fam["seed"]
                print(
                    f"seed identities={s['identities_found']} "
                    f"created={s['created']} skipped={s['skipped_existing']}"
                )
            d = fam["dry_run"]
            print(
                f"dry-run candidates={d['candidates']} NTC={d['not_this_category']} "
                f"ALREADY={d['already_mapped']} EXACT={d['exact']} "
                f"NO_PMC={d['no_pmc_product']} INSUFF={d['insufficient_data']} "
                f"REVIEW={d['review']} AMBIG={d['ambiguous']}"
            )
            a = fam["exact_audit"]
            print(f"exact_audit ok={a['exact_ok']} mismatches={a['mismatch_count']}")
            if fam.get("apply"):
                ap = fam["apply"]
                print(
                    f"apply applied={ap.get('applied')} "
                    f"second={fam.get('apply_second', {}).get('applied')}"
                )
    return 0


def main_mass_batch(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog=f"product_mapping.py {MASS_BATCH_COMMAND}",
        description="Passe mass mapping BRICO_DEPOT (DIMENSIONAL + BOARD + re-apply).",
    )
    p.add_argument("--apply", action="store_true", default=True)
    p.add_argument("--no-apply", action="store_false", dest="apply")
    p.add_argument("--format", default="json", choices=("text", "json"))
    args = p.parse_args(argv)
    settings = Settings()
    engine = make_engine(settings.database_url)
    from app.services.product_mapping.mass_batch import run_mass_pass

    with Session(engine) as session:
        report = run_mass_pass(session, apply=args.apply)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            f"mapped {report['mapped_before']} → {report['mapped_after']} "
            f"(+{report['mapped_after'] - report['mapped_before']})"
        )
        for fam in report["families"]:
            ap = fam.get("apply") or {}
            seed = fam.get("seed") or {}
            print(
                f"{fam['category']}: seed={seed.get('created', '-')} "
                f"exact={fam.get('dry_run', {}).get('exact')} "
                f"applied={ap.get('applied', '-')}"
            )
    return 0


def _print_apply(result) -> None:
    d = result.to_dict()
    print("=== Apply EXACT PLAQUE_PLATRE ===")
    print(f"dry_run={d['dry_run']} analysed={d['analysed']} "
          f"already_mapped={d['already_mapped']} exact_candidates={d['exact_candidates']} "
          f"applied={d['applied']} skipped={d['skipped']} errors={d['errors']}")
    print(json.dumps(d, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    # Dispatch précoce : la CLI historique (--category …) reste inchangée.
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == CATALOG_DISCOVER_COMMAND:
        return main_catalog_discover(raw[1:])
    if raw and raw[0] == CATALOG_BATCH_DISCOVER_COMMAND:
        return main_catalog_batch_discover(raw[1:])
    if raw and raw[0] == CATALOG_BATCH_CONSOLIDATE_COMMAND:
        return main_catalog_batch_consolidate(raw[1:])
    if raw and raw[0] == FASTENER_BATCH_COMMAND:
        return main_fastener_batch(raw[1:])
    if raw and raw[0] == PROFILES_BATCH_COMMAND:
        return main_profiles_batch(raw[1:])
    if raw and raw[0] == MASS_BATCH_COMMAND:
        return main_mass_batch(raw[1:])

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

        if args.category in (CATEGORY_VIS_PLACO, CATEGORY_VIS_AGGLO):
            run = svc.run_category_code(
                args.category,
                supplier_name=args.supplier,
                limit=None if args.all_candidates else args.limit,
                all_candidates=args.all_candidates,
            )
            _print_generic_report(run, get_rule(args.category))
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
