"""Moteur de synchronisation industrielle catalogue Brico Dépôt.

Orchestre : discovery sitemap → snapshot items → batches → enrich GraphQL ≤10
→ upsert SupplierProduct (logique existante) → checkpoint.

Sémantique dry-run :
  - job + items + erreurs + checkpoints SONT persistés ;
  - aucune mutation SupplierProduct / Product / Offer / mappings.

Garde-fou : sans --full, --limit obligatoire ≤ SYNC_VALIDATION_MAX (500).
--full (explicite) découvre l'ensemble du catalogue via sitemaps.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.bricodepot.catalog_discovery import (
    BricoDepotFullDiscoverer,
    CatalogDiscoveryReport,
)
from app.connectors.bricodepot.catalog_dto import (
    BricoDepotCatalogProduct,
    BricoDepotDiscoveredProduct,
)
from app.connectors.bricodepot.catalog_enrich import BricoDepotCatalogEnricher
from app.connectors.bricodepot.client import (
    PRODUCT_SKU_PAGE_SIZE,
    BricoDepotClient,
    BricoDepotClientError,
    BricoDepotGraphQLError,
    BricoDepotHttpError,
    BricoDepotTimeoutError,
)
from app.connectors.bricodepot.connector import CONNECTOR_KEY
from app.models import Supplier, SupplierProduct
from app.models.supplier_catalog_sync import (
    ERROR_DATA,
    ERROR_PERMANENT,
    ERROR_TRANSIENT,
    ITEM_DONE,
    ITEM_FAILED,
    ITEM_PENDING,
    ITEM_SKIPPED,
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_COMPLETED_WITH_ERRORS,
    JOB_FAILED,
    JOB_PAUSED,
    JOB_PENDING,
    JOB_RUNNING,
    SupplierCatalogSyncError,
    SupplierCatalogSyncItem,
    SupplierCatalogSyncJob,
)
from app.services.bricodepot_catalog_import import (
    classify_catalog_action,
    ensure_catalog_import_row,
    resolve_brico_supplier,
    upsert_supplier_product,
)

# Plafond phase développement — catalogue ~27k encore interdit.
SYNC_VALIDATION_MAX = 500
DEFAULT_SYNC_BATCH_SIZE = 100
MIN_SYNC_BATCH_SIZE = 10
MAX_SYNC_BATCH_SIZE = 200
DEFAULT_GRAPHQL_BATCH_SIZE = PRODUCT_SKU_PAGE_SIZE  # 10
MAX_RETRY_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
LEASE_SECONDS = 120

_SECRET_RE = re.compile(
    r"(?i)(authorization|cookie|bearer|x-api-key|x-session-id|x-search-key|"
    r"api[_-]?key|token|password|secret)\s*[:=]\s*\S+"
)
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")


_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_AUTH_BEARER_HEADER_RE = re.compile(
    r"(?i)\bauthorization\s*:\s*bearer\s+\S+"
)


def _utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def sanitize_error_message(exc: BaseException | str, *, max_len: int = 500) -> str:
    """Message d'erreur sans secrets / tokens / headers sensibles."""
    text = str(exc)
    text = _AUTH_BEARER_HEADER_RE.sub("Authorization: Bearer <REDACTED>", text)
    text = _SECRET_RE.sub(r"\1=<REDACTED>", text)
    text = _BEARER_RE.sub("Bearer <REDACTED>", text)
    text = _LONG_TOKEN_RE.sub("<REDACTED>", text)
    text = text.replace("\n", " ").strip()
    return text[:max_len]


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, BricoDepotTimeoutError):
        return ERROR_TRANSIENT
    if isinstance(exc, BricoDepotHttpError):
        if exc.status in {429, 500, 502, 503, 504}:
            return ERROR_TRANSIENT
        return ERROR_PERMANENT
    if isinstance(exc, BricoDepotGraphQLError):
        msg = str(exc).lower()
        if "timeout" in msg or "temporar" in msg:
            return ERROR_TRANSIENT
        return ERROR_DATA
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return ERROR_TRANSIENT
    if isinstance(exc, BricoDepotClientError):
        return ERROR_TRANSIENT
    return ERROR_PERMANENT


def clamp_sync_batch_size(value: int) -> int:
    return max(MIN_SYNC_BATCH_SIZE, min(int(value), MAX_SYNC_BATCH_SIZE))


def clamp_graphql_batch_size(value: int) -> int:
    return max(1, min(int(value), PRODUCT_SKU_PAGE_SIZE))


def assert_limit_allowed(limit: int | None, *, full: bool) -> int | None:
    """Valide limit / full.

    - full=True → limit doit être None ; retourne None (découverte complète).
    - full=False → limit obligatoire in 1..SYNC_VALIDATION_MAX.
    """
    if full:
        if limit is not None:
            raise ValueError("--full et --limit sont mutuellement exclusifs.")
        return None
    if limit is None:
        raise ValueError(
            f"--limit est obligatoire (1..{SYNC_VALIDATION_MAX}) "
            "sauf avec --full explicite."
        )
    if limit < 1:
        raise ValueError(f"limit doit être > 0 (reçu {limit})")
    if limit > SYNC_VALIDATION_MAX:
        raise ValueError(
            f"limit={limit} > plafond mode limité {SYNC_VALIDATION_MAX}. "
            "Utiliser --full pour le catalogue complet."
        )
    return int(limit)


@dataclass
class SyncBatchProgress:
    batch_index: int
    batches_total: int
    processed: int
    created: int
    updated: int
    unchanged: int
    failed: int
    skipped: int
    elapsed_s: float
    rate: float


@dataclass
class SyncRunResult:
    job_id: int
    status: str
    dry_run: bool
    discovered_total: int = 0
    processed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0
    graphql_calls: int = 0
    retries: int = 0
    checkpoint_index: int = 0
    duration_s: float = 0.0
    errors_sample: list[str] = field(default_factory=list)
    resume_hint: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "discovered_total": self.discovered_total,
            "processed": self.processed,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "skipped": self.skipped,
            "graphql_calls": self.graphql_calls,
            "retries": self.retries,
            "checkpoint_index": self.checkpoint_index,
            "duration_s": round(self.duration_s, 3),
            "average_rate": round(
                self.processed / self.duration_s, 2
            )
            if self.duration_s > 0
            else None,
            "errors_sample": list(self.errors_sample),
            "resume_hint": self.resume_hint,
        }


ProgressCallback = Callable[[SyncBatchProgress], None]


class InterruptRequested(Exception):
    """SIGINT / annulation coopérative."""


class JobLockError(Exception):
    """Impossible d'acquérir le lease du job."""


class BricoDepotCatalogSyncService:
    """Orchestrateur sync catalogue Brico — réutilise enrich + upsert existants."""

    def __init__(
        self,
        session: Session,
        *,
        discoverer: BricoDepotFullDiscoverer | None = None,
        enricher: BricoDepotCatalogEnricher | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        worker_id: str | None = None,
    ):
        self.session = session
        self.discoverer = discoverer or BricoDepotFullDiscoverer()
        self.enricher = enricher or BricoDepotCatalogEnricher(BricoDepotClient())
        self.sleep_fn = sleep_fn
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._interrupt = False
        self._retries = 0
        self._graphql_calls = 0

    def request_interrupt(self) -> None:
        self._interrupt = True

    # --- API publique -----------------------------------------------------

    def create_job(
        self,
        *,
        limit: int | None,
        dry_run: bool = True,
        full: bool = False,
        sync_batch_size: int = DEFAULT_SYNC_BATCH_SIZE,
        graphql_batch_size: int = DEFAULT_GRAPHQL_BATCH_SIZE,
        discovery: CatalogDiscoveryReport | None = None,
    ) -> SupplierCatalogSyncJob:
        limit_ok = assert_limit_allowed(limit, full=full)
        sync_batch_size = clamp_sync_batch_size(sync_batch_size)
        graphql_batch_size = clamp_graphql_batch_size(graphql_batch_size)

        report = discovery or self.discoverer.discover_all(limit=limit_ok)
        if report.errors and not report.products:
            raise RuntimeError(f"discovery failed: {report.errors}")

        supplier = resolve_brico_supplier(self.session)
        job = SupplierCatalogSyncJob(
            connector_key=CONNECTOR_KEY,
            supplier_id=supplier.id,
            mode="full" if full else "limited",
            status=JOB_PENDING,
            dry_run=bool(dry_run),
            discovered_total=len(report.products),
            sync_batch_size=sync_batch_size,
            graphql_batch_size=graphql_batch_size,
            limit_skus=limit_ok,
            job_metadata={
                "discovery": report.to_dict(),
                "sitemap_reference_approx": 27322,
                "full": bool(full),
            },
        )
        self.session.add(job)
        self.session.flush()

        for pos, disc in enumerate(report.products):
            self.session.add(
                SupplierCatalogSyncItem(
                    job_id=job.id,
                    position=pos,
                    supplier_reference=disc.supplier_reference[:60],
                    status=ITEM_PENDING,
                )
            )
        self.session.flush()
        return job

    def get_job(self, job_id: int) -> SupplierCatalogSyncJob | None:
        return self.session.get(SupplierCatalogSyncJob, job_id)

    def job_status_dict(self, job_id: int) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"job {job_id} introuvable")
        return {
            "id": job.id,
            "status": job.status,
            "dry_run": job.dry_run,
            "mode": job.mode,
            "discovered_total": job.discovered_total,
            "processed_count": job.processed_count,
            "created_count": job.created_count,
            "updated_count": job.updated_count,
            "unchanged_count": job.unchanged_count,
            "failed_count": job.failed_count,
            "skipped_count": job.skipped_count,
            "graphql_calls": job.graphql_calls,
            "checkpoint_index": job.checkpoint_index,
            "limit_skus": job.limit_skus,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "lease_owner": job.lease_owner,
            "lease_until": job.lease_until.isoformat() if job.lease_until else None,
            "metadata": job.job_metadata,
            "error_summary": job.error_summary,
        }

    def run_job(
        self,
        job_id: int,
        *,
        progress: ProgressCallback | None = None,
        retry_failed_only: bool = False,
    ) -> SyncRunResult:
        self._interrupt = False
        self._retries = 0
        started = self.clock()
        job = self._acquire_lease(job_id)
        job.status = JOB_RUNNING
        if job.started_at is None:
            job.started_at = started
        self.session.commit()

        try:
            self._run_batches(job, progress=progress, retry_failed_only=retry_failed_only)
        except InterruptRequested:
            self._refresh_job(job)
            job.status = JOB_PAUSED
            job.lease_owner = None
            job.lease_until = None
            self.session.commit()
            return self._result_from_job(
                job,
                duration_s=(self.clock() - started).total_seconds(),
                resume_hint=(
                    f"python tools/bricodepot_catalog_sync.py --resume {job.id}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._refresh_job(job)
            job.status = JOB_FAILED
            job.error_summary = {
                "fatal": sanitize_error_message(exc),
                "type": classify_exception(exc),
            }
            job.lease_owner = None
            job.lease_until = None
            job.completed_at = self.clock()
            self.session.commit()
            raise

        self._refresh_job(job)
        job.lease_owner = None
        job.lease_until = None
        job.completed_at = self.clock()
        if job.failed_count > 0:
            job.status = JOB_COMPLETED_WITH_ERRORS
        else:
            job.status = JOB_COMPLETED
        self.session.commit()
        return self._result_from_job(
            job, duration_s=(self.clock() - started).total_seconds()
        )

    # --- interne ----------------------------------------------------------

    def _acquire_lease(self, job_id: int) -> SupplierCatalogSyncJob:
        job = self.session.execute(
            select(SupplierCatalogSyncJob)
            .where(SupplierCatalogSyncJob.id == job_id)
            .with_for_update()
        ).scalar_one_or_none()
        if job is None:
            raise ValueError(f"job {job_id} introuvable")
        if job.status in {JOB_COMPLETED, JOB_CANCELLED}:
            raise JobLockError(f"job {job_id} déjà terminé ({job.status})")
        now = _utc_aware(self.clock())
        lease_until = (
            _utc_aware(job.lease_until) if job.lease_until is not None else None
        )
        if (
            job.lease_owner
            and lease_until
            and lease_until > now
            and job.lease_owner != self.worker_id
        ):
            raise JobLockError(
                f"job {job_id} verrouillé par {job.lease_owner} jusqu'à {job.lease_until}"
            )
        job.lease_owner = self.worker_id
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        self.session.flush()
        return job

    def _refresh_lease(self, job: SupplierCatalogSyncJob) -> None:
        job.lease_until = self.clock() + timedelta(seconds=LEASE_SECONDS)
        self.session.flush()

    def _refresh_job(self, job: SupplierCatalogSyncJob) -> None:
        self.session.refresh(job)

    def _run_batches(
        self,
        job: SupplierCatalogSyncJob,
        *,
        progress: ProgressCallback | None,
        retry_failed_only: bool,
    ) -> None:
        total = job.discovered_total
        batch_size = job.sync_batch_size
        batches_total = max(1, (total + batch_size - 1) // batch_size) if total else 0
        batch_index = 0
        t0 = self.clock()

        while True:
            if self._interrupt:
                raise InterruptRequested()

            items = self._next_batch_items(
                job, retry_failed_only=retry_failed_only
            )
            if not items:
                break

            batch_index += 1
            self._process_one_batch(job, items, batch_index=batch_index)
            self._refresh_lease(job)
            self.session.commit()

            if progress:
                elapsed = (self.clock() - t0).total_seconds()
                processed = job.processed_count
                progress(
                    SyncBatchProgress(
                        batch_index=batch_index,
                        batches_total=batches_total,
                        processed=processed,
                        created=job.created_count,
                        updated=job.updated_count,
                        unchanged=job.unchanged_count,
                        failed=job.failed_count,
                        skipped=job.skipped_count,
                        elapsed_s=elapsed,
                        rate=(processed / elapsed) if elapsed > 0 else 0.0,
                    )
                )

    def _next_batch_items(
        self, job: SupplierCatalogSyncJob, *, retry_failed_only: bool
    ) -> list[SupplierCatalogSyncItem]:
        if retry_failed_only:
            q = (
                select(SupplierCatalogSyncItem)
                .where(
                    SupplierCatalogSyncItem.job_id == job.id,
                    SupplierCatalogSyncItem.status == ITEM_FAILED,
                )
                .order_by(SupplierCatalogSyncItem.position)
                .limit(job.sync_batch_size)
            )
        else:
            q = (
                select(SupplierCatalogSyncItem)
                .where(
                    SupplierCatalogSyncItem.job_id == job.id,
                    SupplierCatalogSyncItem.position >= job.checkpoint_index,
                    SupplierCatalogSyncItem.status.in_((ITEM_PENDING, ITEM_FAILED)),
                )
                .order_by(SupplierCatalogSyncItem.position)
                .limit(job.sync_batch_size)
            )
        return list(self.session.scalars(q).all())

    def _process_one_batch(
        self,
        job: SupplierCatalogSyncJob,
        items: list[SupplierCatalogSyncItem],
        *,
        batch_index: int,
    ) -> None:
        discovered = [
            BricoDepotDiscoveredProduct(
                supplier_reference=it.supplier_reference,
                product_url=f"https://www.bricodepot.fr/p/{it.supplier_reference}",
                slug=None,
            )
            for it in items
        ]
        products, missing = self._enrich_with_retry(discovered)
        missing_set = set(missing)
        by_sku = {p.supplier_reference: p for p in products}

        supplier = None
        catalog_id = None
        if not job.dry_run:
            supplier = resolve_brico_supplier(self.session)
            catalog = ensure_catalog_import_row(
                self.session,
                filename=f"bricodepot-sync-job{job.id}",
                row_count=job.discovered_total,
            )
            catalog_id = catalog.id

        existing: dict[str, SupplierProduct] = {}
        if job.supplier_id is not None:
            refs = [it.supplier_reference for it in items]
            rows = self.session.scalars(
                select(SupplierProduct).where(
                    SupplierProduct.supplier_id == job.supplier_id,
                    SupplierProduct.supplier_reference.in_(refs),
                )
            ).all()
            existing = {r.supplier_reference: r for r in rows}

        max_pos = job.checkpoint_index
        for it in items:
            product = by_sku.get(it.supplier_reference)
            is_missing = it.supplier_reference in missing_set or product is None
            if product is None:
                product = BricoDepotCatalogProduct(
                    supplier_reference=it.supplier_reference,
                    product_url=f"https://www.bricodepot.fr/p/{it.supplier_reference}",
                )
            try:
                action = self._handle_item(
                    job,
                    it,
                    product=product,
                    missing=is_missing,
                    supplier=supplier,
                    catalog_id=catalog_id,
                    existing=existing.get(it.supplier_reference),
                )
                it.last_action = action
                it.attempts = (it.attempts or 0) + 1
                if action == "skipped":
                    it.status = ITEM_SKIPPED
                    job.skipped_count += 1
                else:
                    it.status = ITEM_DONE
                    it.last_error = None
                    if action == "created":
                        job.created_count += 1
                    elif action == "updated":
                        job.updated_count += 1
                    elif action == "unchanged":
                        job.unchanged_count += 1
                job.processed_count += 1
            except Exception as exc:  # noqa: BLE001
                self._record_item_failure(
                    job, it, exc=exc, batch_index=batch_index
                )
            max_pos = max(max_pos, it.position + 1)

        # Checkpoint = prochain index après le dernier item du batch traité.
        job.checkpoint_index = max(job.checkpoint_index, max_pos)
        job.graphql_calls = self._graphql_calls
        self.session.flush()

    def _handle_item(
        self,
        job: SupplierCatalogSyncJob,
        item: SupplierCatalogSyncItem,
        *,
        product: BricoDepotCatalogProduct,
        missing: bool,
        supplier: Supplier | None,
        catalog_id: int | None,
        existing: SupplierProduct | None,
    ) -> str:
        if job.dry_run:
            action = classify_catalog_action(existing, product, missing=missing)
            return action

        assert supplier is not None
        action, _preserved = upsert_supplier_product(
            self.session,
            supplier=supplier,
            product=product,
            catalog_id=catalog_id,
        )
        return action

    def _record_item_failure(
        self,
        job: SupplierCatalogSyncJob,
        item: SupplierCatalogSyncItem,
        *,
        exc: BaseException,
        batch_index: int,
    ) -> None:
        err_type = classify_exception(exc)
        msg = sanitize_error_message(exc)
        item.status = ITEM_FAILED
        item.attempts = (item.attempts or 0) + 1
        item.last_error = msg
        job.failed_count += 1
        job.processed_count += 1
        self.session.add(
            SupplierCatalogSyncError(
                job_id=job.id,
                supplier_reference=item.supplier_reference,
                batch_index=batch_index,
                error_type=err_type,
                message=msg,
                attempts=item.attempts,
            )
        )

    def _enrich_with_retry(
        self, discovered: list[BricoDepotDiscoveredProduct]
    ) -> tuple[list[BricoDepotCatalogProduct], list[str]]:
        """Enrichit avec retry sur erreurs transitoires (batch entier)."""
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                # Compter les appels GraphQL approximatifs
                n = len(discovered)
                self._graphql_calls += max(
                    1, (n + PRODUCT_SKU_PAGE_SIZE - 1) // PRODUCT_SKU_PAGE_SIZE
                ) if n else 0
                return self.enricher.enrich(discovered)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                kind = classify_exception(exc)
                if kind != ERROR_TRANSIENT or attempt >= MAX_RETRY_ATTEMPTS - 1:
                    raise
                self._retries += 1
                delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                # Retry-After raisonnable si présent sur HttpError.detail
                if isinstance(exc, BricoDepotHttpError) and exc.status == 429:
                    delay = max(delay, 2.0)
                self.sleep_fn(delay)
        assert last_exc is not None
        raise last_exc

    def _result_from_job(
        self,
        job: SupplierCatalogSyncJob,
        *,
        duration_s: float,
        resume_hint: str | None = None,
    ) -> SyncRunResult:
        errors = list(
            self.session.scalars(
                select(SupplierCatalogSyncError.message)
                .where(SupplierCatalogSyncError.job_id == job.id)
                .order_by(SupplierCatalogSyncError.id.desc())
                .limit(5)
            ).all()
        )
        return SyncRunResult(
            job_id=job.id,
            status=job.status,
            dry_run=job.dry_run,
            discovered_total=job.discovered_total,
            processed=job.processed_count,
            created=job.created_count,
            updated=job.updated_count,
            unchanged=job.unchanged_count,
            failed=job.failed_count,
            skipped=job.skipped_count,
            graphql_calls=job.graphql_calls,
            retries=self._retries,
            checkpoint_index=job.checkpoint_index,
            duration_s=duration_s,
            errors_sample=errors,
            resume_hint=resume_hint,
        )
