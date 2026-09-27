"""Jobs de synchronisation catalogue fournisseur (Brico Dépôt et futurs).

create_all additif — aucune migration destructive.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# Statuts job
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_PAUSED = "paused"
JOB_COMPLETED = "completed"
JOB_COMPLETED_WITH_ERRORS = "completed_with_errors"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

# Statuts item
ITEM_PENDING = "pending"
ITEM_DONE = "done"
ITEM_FAILED = "failed"
ITEM_SKIPPED = "skipped"

# Types d'erreur
ERROR_TRANSIENT = "transient"
ERROR_PERMANENT = "permanent"
ERROR_DATA = "data"


class SupplierCatalogSyncJob(Base):
    """Job de sync catalogue — snapshot SKU + checkpoints + stats."""

    __tablename__ = "supplier_catalog_sync_jobs"
    __table_args__ = (
        Index("ix_catalog_sync_jobs_status", "status"),
        Index("ix_catalog_sync_jobs_connector", "connector_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connector_key: Mapped[str] = mapped_column(String(80), index=True)
    supplier_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("suppliers.id"), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(40), default="limited")  # limited | full
    status: Mapped[str] = mapped_column(String(40), default=JOB_PENDING)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    discovered_total: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    graphql_calls: Mapped[int] = mapped_column(Integer, default=0)

    # Prochain index (0-based) à traiter — avance uniquement après COMMIT batch.
    checkpoint_index: Mapped[int] = mapped_column(Integer, default=0)
    sync_batch_size: Mapped[int] = mapped_column(Integer, default=100)
    graphql_batch_size: Mapped[int] = mapped_column(Integer, default=10)
    limit_skus: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True, default=None)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    error_summary: Mapped[dict | list | None] = mapped_column(JSON, nullable=True, default=None)
    job_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupplierCatalogSyncItem(Base):
    """SKU snapshot d'un job — population stable pour reprise déterministe."""

    __tablename__ = "supplier_catalog_sync_items"
    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_catalog_sync_item_pos"),
        UniqueConstraint("job_id", "supplier_reference", name="uq_catalog_sync_item_ref"),
        Index("ix_catalog_sync_items_job_status", "job_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_catalog_sync_jobs.id"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    supplier_reference: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default=ITEM_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    last_action: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )  # created|updated|unchanged|skipped

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupplierCatalogSyncError(Base):
    """Erreur isolée — messages sanitizés, jamais de secrets."""

    __tablename__ = "supplier_catalog_sync_errors"
    __table_args__ = (
        Index("ix_catalog_sync_errors_job", "job_id"),
        Index("ix_catalog_sync_errors_ref", "supplier_reference"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_catalog_sync_jobs.id"), index=True
    )
    supplier_reference: Mapped[str | None] = mapped_column(
        String(60), nullable=True, default=None
    )
    batch_index: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    error_type: Mapped[str] = mapped_column(String(20), default=ERROR_PERMANENT)
    message: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
