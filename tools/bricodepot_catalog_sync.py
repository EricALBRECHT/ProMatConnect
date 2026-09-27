#!/usr/bin/env python3
"""Synchronisation industrielle catalogue Brico Dépôt.

Dry-run par défaut pour la création de job.
Job + checkpoints persistés même en dry-run ; aucune mutation catalogue métier
(SupplierProduct / Product / Offer) si dry_run=True.

Garde-fous :
  --limit obligatoire en mode limité (1..500)
  --full explicite pour découvrir/synchroniser l'ensemble du catalogue (~27k)
  --full et --limit sont mutuellement exclusifs

Exemples :
  python tools/bricodepot_catalog_sync.py --dry-run --limit 100
  python tools/bricodepot_catalog_sync.py --limit 100 --apply
  python tools/bricodepot_catalog_sync.py --full --apply
  python tools/bricodepot_catalog_sync.py --resume 12
  python tools/bricodepot_catalog_sync.py --status 12
  python tools/bricodepot_catalog_sync.py --retry-errors 12
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import make_engine
from app.services.bricodepot_catalog_sync import (
    SYNC_VALIDATION_MAX,
    BricoDepotCatalogSyncService,
    InterruptRequested,
    JobLockError,
    SyncBatchProgress,
    assert_limit_allowed,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"Mode limité : 1..{SYNC_VALIDATION_MAX} (incompatible avec --full)",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Découverte + sync de l'ensemble du catalogue (sitemaps)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Créer/exécuter un job sans écrire le catalogue métier",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Écriture SupplierProduct (implique dry_run=False)",
    )
    p.add_argument("--resume", type=int, default=None, metavar="JOB_ID")
    p.add_argument("--status", type=int, default=None, metavar="JOB_ID")
    p.add_argument("--retry-errors", type=int, default=None, metavar="JOB_ID")
    p.add_argument("--sync-batch-size", type=int, default=100)
    p.add_argument("--graphql-batch-size", type=int, default=10)
    return p.parse_args(argv)


def _print_progress(prog: SyncBatchProgress) -> None:
    print(
        f"[{prog.batch_index}/{prog.batches_total}] "
        f"processed={prog.processed} "
        f"created={prog.created} updated={prog.updated} "
        f"unchanged={prog.unchanged} failed={prog.failed} "
        f"elapsed={prog.elapsed_s:.1f}s rate={prog.rate:.1f}/s",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.status is not None:
        settings = Settings()
        engine = make_engine(settings.database_url)
        with Session(engine) as session:
            svc = BricoDepotCatalogSyncService(session)
            try:
                print(json.dumps(svc.job_status_dict(args.status), ensure_ascii=False, indent=2))
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        return 0

    settings = Settings()
    engine = make_engine(settings.database_url)

    with Session(engine) as session:
        svc = BricoDepotCatalogSyncService(session)

        def _on_sigint(_signum, _frame):
            svc.request_interrupt()
            print("\nSIGINT reçu — fin du batch courant puis pause…", flush=True)

        signal.signal(signal.SIGINT, _on_sigint)

        retry_failed = False
        job_id: int | None = None

        if args.resume is not None:
            job_id = args.resume
        elif args.retry_errors is not None:
            job_id = args.retry_errors
            retry_failed = True
        else:
            if args.apply and args.dry_run:
                print("Refusé : --apply et --dry-run sont exclusifs.", file=sys.stderr)
                return 2
            dry_run = not args.apply
            if not args.apply and not args.dry_run:
                dry_run = True
            try:
                limit = assert_limit_allowed(args.limit, full=bool(args.full))
            except ValueError as exc:
                print(f"Refusé : {exc}", file=sys.stderr)
                return 2
            mode = "full" if args.full else f"limit={limit}"
            print(
                f"Découverte sitemap ({mode}, dry_run={dry_run})…",
                flush=True,
            )
            t0 = time.monotonic()
            job = svc.create_job(
                limit=limit,
                dry_run=dry_run,
                full=bool(args.full),
                sync_batch_size=args.sync_batch_size,
                graphql_batch_size=args.graphql_batch_size,
            )
            session.commit()
            job_id = job.id
            print(
                f"Job {job_id} créé : mode={job.mode} discovered={job.discovered_total} "
                f"en {time.monotonic() - t0:.1f}s",
                flush=True,
            )

        assert job_id is not None
        try:
            result = svc.run_job(
                job_id,
                progress=_print_progress,
                retry_failed_only=retry_failed,
            )
        except JobLockError as exc:
            print(f"Refusé : {exc}", file=sys.stderr)
            return 2
        except InterruptRequested:
            print(json.dumps({"status": "paused", "job_id": job_id}, indent=2))
            return 130

        print("\n=== Résultat ===")
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if result.resume_hint:
            print(f"\nResume:\n{result.resume_hint}")
        if result.status == "paused":
            return 130
        if result.status == "failed":
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
