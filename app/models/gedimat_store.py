"""Référentiel des magasins Gedimat (fiche publique), distinct du catalogue produit.

gedimat_id est l'identifiant du site (préfixe d'URL, cookie mm_idMag).
algolia_store_id est le filtre Catalog Algolia. idEntrepot n'est pas stocké :
ce n'est ni l'un ni l'autre.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GedimatStore(Base):
    __tablename__ = "gedimat_stores"
    __table_args__ = (
        CheckConstraint("latitude IS NULL OR (latitude >= -90 AND latitude <= 90)"),
        CheckConstraint("longitude IS NULL OR (longitude >= -180 AND longitude <= 180)"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    gedimat_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    algolia_store_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")
    postal_code: Mapped[str] = mapped_column(String(10), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    ecommerce: Mapped[bool] = mapped_column(Boolean, default=False)
    store_type: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
