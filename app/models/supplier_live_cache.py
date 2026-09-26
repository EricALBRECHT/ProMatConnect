"""Cache serveur partagé des données fournisseurs LIVE (magasins + offres).

Distinct des snapshots d'approvisionnement retenu et du géocodage.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SupplierStoreCache(Base):
    """Cache store-locator : connector_key + lookup_key (ex. INSEE)."""

    __tablename__ = "supplier_store_cache"
    __table_args__ = (
        UniqueConstraint("connector_key", "lookup_key", name="uq_supplier_store_cache_key"),
        Index("ix_supplier_store_cache_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connector_key: Mapped[str] = mapped_column(String(80), index=True)
    lookup_key: Mapped[str] = mapped_column(String(120))
    # Liste normalisée de magasins/retailers (JSON) — jamais de cookies/tokens.
    payload: Mapped[dict | list] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Anti-stampede : lease court pendant un refresh réseau.
    refresh_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupplierOfferCache(Base):
    """Cache offres LIVE : une ligne par connector × dépôt × SKU.

    Fraîcheur prix et stock indépendantes (price_expires_at / stock_expires_at).
    """

    __tablename__ = "supplier_offer_cache"
    __table_args__ = (
        UniqueConstraint(
            "connector_key",
            "external_store_id",
            "supplier_reference",
            name="uq_supplier_offer_cache_key",
        ),
        Index("ix_supplier_offer_cache_price_exp", "price_expires_at"),
        Index("ix_supplier_offer_cache_stock_exp", "stock_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connector_key: Mapped[str] = mapped_column(String(80), index=True)
    external_store_id: Mapped[str] = mapped_column(String(80))
    supplier_reference: Mapped[str] = mapped_column(String(80))
    seller_code: Mapped[str | None] = mapped_column(String(80), nullable=True, default=None)

    price_ht: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    price_ttc: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    stock_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stock_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_salable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_offer_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    packaging_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    reference_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    supplier_unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Métadonnées non sensibles (ex. prix mesure) — jamais cookies/headers.
    extras: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

    price_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stock_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    refresh_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
