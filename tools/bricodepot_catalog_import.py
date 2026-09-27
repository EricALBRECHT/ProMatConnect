#!/usr/bin/env python3
"""Import catalogue Brico Dépôt (sitemap + Magento) — paliers contrôlés.

Dry-run par défaut (aucune écriture DB).
Écriture uniquement avec --apply explicite.

Modes (mutuellement exclusifs pour la sélection) :
  --limit N              : N premières URLs sitemap (ordre découverte)
  --sample-diverse N     : échantillon round-robin multi-familles Magento

Plafond phase actuelle : VALIDATION_IMPORT_MAX (500). Catalogue ~27k interdit.

Exemples :
  python tools/bricodepot_catalog_import.py --limit 50
  python tools/bricodepot_catalog_import.py --sample-diverse 500
  python tools/bricodepot_catalog_import.py --sample-diverse 500 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.bricodepot.catalog import (
    VALIDATION_IMPORT_MAX,
    BricoDepotCatalogService,
)
from app.connectors.bricodepot.catalog_dto import BricoDepotCatalogProduct
from app.connectors.bricodepot.catalog_enrich import BricoDepotCatalogEnricher
from app.connectors.bricodepot.catalog_sample import concentration_stats
from app.connectors.bricodepot.client import BricoDepotClient
from app.database import make_engine
from app.services.bricodepot_catalog_import import BricoDepotCatalogImportService


def _top_category(path: str | None) -> str:
    if not path:
        return "(sans catégorie)"
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return "(sans catégorie)"
    if parts[0] == "produits" and len(parts) >= 2:
        return parts[1]
    return parts[0]


def audit_catalog_dtos(
    products: list[BricoDepotCatalogProduct],
    *,
    family_by_sku: dict[str, str] | None = None,
    family_inventory: list[dict] | None = None,
    graphql_calls_sample: int | None = None,
) -> dict:
    """Statistiques qualité + diversité — aucune correction des données source."""
    refs = [p.supplier_reference for p in products]
    eans = [p.ean for p in products if p.ean]
    ean_counts = Counter(eans)

    def examples(pred, *, n: int = 5) -> list[dict]:
        out = []
        for p in products:
            if not pred(p):
                continue
            out.append(
                {
                    "supplier_reference": p.supplier_reference,
                    "name": p.name,
                    "ean": p.ean,
                    "category_path": p.category_path,
                    "packaging_label": p.packaging_label,
                    "content_net_value": str(p.content_net_value)
                    if p.content_net_value is not None
                    else None,
                    "content_net_unit": p.content_net_unit,
                    "sap_code": p.sap_code,
                    "image_url": p.image_url,
                }
            )
            if len(out) >= n:
                break
        return out

    # Famille d'échantillonnage (si fournie) sinon category_path Magento enrichi.
    sample_counts: dict[str, int] = Counter()
    enrich_counts: dict[str, int] = Counter()
    examples_by_family: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        fam_sample = (family_by_sku or {}).get(p.supplier_reference)
        fam_enrich = _top_category(p.category_path)
        fam = fam_sample or fam_enrich
        sample_counts[fam] += 1
        enrich_counts[fam_enrich] += 1
        if len(examples_by_family[fam]) < 3:
            examples_by_family[fam].append(
                {
                    "supplier_reference": p.supplier_reference,
                    "name": p.name,
                    "category_path": p.category_path,
                    "ean": p.ean,
                }
            )

    concentration = concentration_stats(dict(sample_counts))
    enrich_concentration = concentration_stats(dict(enrich_counts))

    duplicate_eans = {ean: count for ean, count in ean_counts.items() if count > 1}

    return {
        "count": len(products),
        "unique_skus": len(set(refs)),
        "quality": {
            "missing_ean": sum(1 for p in products if not p.ean),
            "missing_name": sum(1 for p in products if not p.name),
            "missing_image": sum(1 for p in products if not p.image_url),
            "missing_category": sum(1 for p in products if not p.category_path),
            "missing_packaging_label": sum(
                1 for p in products if not p.packaging_label
            ),
            "missing_content_net_value": sum(
                1 for p in products if p.content_net_value is None
            ),
            "content_net_value_zero": sum(
                1
                for p in products
                if p.content_net_value is not None
                and p.content_net_value == Decimal("0")
            ),
            "missing_content_net_unit": sum(
                1 for p in products if not p.content_net_unit
            ),
            "missing_sap_code": sum(1 for p in products if not p.sap_code),
            "duplicate_ean_values": len(duplicate_eans),
            "duplicate_ean_extra_rows": sum(c - 1 for c in duplicate_eans.values()),
        },
        "examples": {
            "missing_ean": examples(lambda p: not p.ean),
            "missing_name": examples(lambda p: not p.name),
            "missing_image": examples(lambda p: not p.image_url),
            "missing_category": examples(lambda p: not p.category_path),
            "missing_packaging_label": examples(lambda p: not p.packaging_label),
            "missing_content_net_value": examples(
                lambda p: p.content_net_value is None
            ),
            "content_net_value_zero": examples(
                lambda p: p.content_net_value is not None
                and p.content_net_value == Decimal("0")
            ),
            "missing_content_net_unit": examples(lambda p: not p.content_net_unit),
            "missing_sap_code": examples(lambda p: not p.sap_code),
            "duplicate_ean": [
                {"ean": ean, "count": count}
                for ean, count in sorted(
                    duplicate_eans.items(), key=lambda kv: (-kv[1], kv[0])
                )[:10]
            ],
            "by_family": dict(sorted(examples_by_family.items())),
        },
        "family_inventory": family_inventory or [],
        "families_available": len(family_inventory or []),
        "sample_distribution": concentration["distribution"],
        "families_represented": concentration["families_represented"],
        "top1_category_pct": concentration["top1_category_pct"],
        "top2_category_pct": concentration["top2_category_pct"],
        "diversity_warning": concentration["diversity_warning"],
        "enrich_category_distribution": enrich_concentration["distribution"],
        "enrich_top1_category_pct": enrich_concentration["top1_category_pct"],
        "enrich_top2_category_pct": enrich_concentration["top2_category_pct"],
        "graphql_calls_sample": graphql_calls_sample,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"N premières URLs sitemap (1..{VALIDATION_IMPORT_MAX})",
    )
    parser.add_argument(
        "--sample-diverse",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Échantillon round-robin multi-familles (1..{VALIDATION_IMPORT_MAX}). "
            "Incompatible avec --limit."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Écriture DB explicite (sinon dry-run)",
    )
    parser.add_argument(
        "--seller-id",
        default="10",
        help="Contexte Magento public (défaut Amiens entity_id=10)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        default=True,
        help="Afficher audit qualité/diversité des DTO (défaut: oui)",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Ne pas afficher l'audit DTO",
    )
    return parser.parse_args(argv)


def _resolve_selection(args: argparse.Namespace) -> tuple[int, bool] | int:
    """Retourne (limit, sample_diverse) ou code erreur 2."""
    if args.limit is not None and args.sample_diverse is not None:
        print(
            "Refusé : --limit et --sample-diverse sont mutuellement exclusifs.",
            file=sys.stderr,
        )
        return 2
    if args.limit is None and args.sample_diverse is None:
        print(
            f"Refusé : indiquer --limit N ou --sample-diverse N "
            f"(1..{VALIDATION_IMPORT_MAX}). Import sans limite interdit.",
            file=sys.stderr,
        )
        return 2

    sample_diverse = args.sample_diverse is not None
    limit = args.sample_diverse if sample_diverse else args.limit
    assert limit is not None
    if limit <= 0:
        print(f"Refusé : N doit être > 0 (reçu {limit})", file=sys.stderr)
        return 2
    if limit > VALIDATION_IMPORT_MAX:
        print(
            f"Refusé : N={limit} > plafond phase actuelle "
            f"{VALIDATION_IMPORT_MAX} (catalogue ~27k interdit).",
            file=sys.stderr,
        )
        return 2
    if args.apply and limit is None:
        print("Refusé : --apply exige --limit ou --sample-diverse.", file=sys.stderr)
        return 2
    return limit, sample_diverse


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    resolved = _resolve_selection(args)
    if isinstance(resolved, int):
        return resolved
    limit, sample_diverse = resolved

    catalog_service = BricoDepotCatalogService(
        enricher=BricoDepotCatalogEnricher(
            BricoDepotClient(), seller_id=args.seller_id
        ),
        seller_id=args.seller_id,
    )
    settings = Settings()
    engine = make_engine(settings.database_url)

    with Session(engine) as session:
        service = BricoDepotCatalogImportService(
            session, catalog_service=catalog_service
        )
        preview = service.preview(limit=limit, sample_diverse=sample_diverse)
        mode = "sample-diverse" if sample_diverse else "sitemap-head"
        print(f"=== Prévisualisation catalogue Brico Dépôt ({mode}) ===")
        print(json.dumps(preview.to_dict(), ensure_ascii=False, indent=2))

        do_audit = args.audit and not args.no_audit
        if do_audit and preview.products:
            print("\n=== Audit qualité / diversité DTO ===")
            print(
                json.dumps(
                    audit_catalog_dtos(
                        preview.products,
                        family_by_sku=preview.family_by_sku or None,
                        family_inventory=preview.family_inventory or None,
                        graphql_calls_sample=preview.graphql_calls_sample or None,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )

        if not args.apply:
            print("\nDRY-RUN : aucune écriture DB. Relancer avec --apply pour importer.")
            return 0 if not preview.errors else 1

        print("\n=== APPLY (écriture DB) ===")
        result = service.apply(
            limit=limit,
            products=preview.products,
            missing_skus=preview.missing_skus,
            sample_diverse=sample_diverse,
        )
        session.commit()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
