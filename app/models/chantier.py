from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models import Product, Timestamps


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Chantier(Timestamps, Base):
    __tablename__ = "chantiers"
    __table_args__ = (
        CheckConstraint("length(trim(nom)) > 0", name="ck_chantier_nom"),
        CheckConstraint("length(trim(adresse)) > 0", name="ck_chantier_adresse"),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_chantier_coords",
        ),
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_chantier_latitude"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_chantier_longitude"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    nom: Mapped[str] = mapped_column(String(200))
    client: Mapped[str | None] = mapped_column(String(200))
    adresse: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    date_prevue: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    # Précision microseconde : ce timestamp est aussi le jeton de concurrence.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    materiaux: Mapped[list["ChantierMaterial"]] = relationship(
        back_populates="chantier",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChantierMaterial.ordre",
    )
    approvisionnement: Mapped["ApprovisionnementRetenu | None"] = relationship(
        back_populates="chantier",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class ChantierMaterial(Base):
    __tablename__ = "chantier_materials"
    __table_args__ = (
        UniqueConstraint("chantier_id", "product_id", name="uq_chantier_product"),
        UniqueConstraint("chantier_id", "ordre", name="uq_chantier_ordre"),
        CheckConstraint("quantite > 0 AND quantite <= 1000000", name="ck_chantier_quantite"),
        CheckConstraint("ordre >= 0", name="ck_chantier_ordre"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    chantier_id: Mapped[int] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"),
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        index=True,
    )
    quantite: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    ordre: Mapped[int]
    chantier: Mapped[Chantier] = relationship(back_populates="materiaux")
    produit: Mapped[Product] = relationship()

    @property
    def unite(self) -> str:
        return self.produit.reference_unit

    @property
    def product_name(self) -> str:
        return self.produit.name

    @property
    def product_code(self) -> str:
        return self.produit.code

    @property
    def product_category(self) -> str:
        return self.produit.category


class ApprovisionnementRetenu(Timestamps, Base):
    """Snapshot commercial d'une stratégie de comparaison retenue pour un chantier."""

    __tablename__ = "approvisionnements_retenus"
    __table_args__ = (
        UniqueConstraint("chantier_id", name="uq_appro_chantier"),
        CheckConstraint(
            "strategy_key IN ('single_stop', 'minimum_materials', 'best_compromise')",
            name="ck_appro_strategy_key",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    chantier_id: Mapped[int] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"),
        index=True,
    )
    strategy_key: Mapped[str] = mapped_column(String(32))
    strategy_title: Mapped[str] = mapped_column(String(200))
    chosen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # Empreinte des besoins au moment du choix (product_id:quantite triés).
    needs_fingerprint: Mapped[str] = mapped_column(String(4000))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    tax_basis: Mapped[str] = mapped_column(String(8), default="HT")
    material_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    estimated_procurement_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    travel_minutes: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    # JSON : strategy + origin + cost_parameters (valeurs snapshotées).
    snapshot: Mapped[dict] = mapped_column(JSON)
    chantier: Mapped[Chantier] = relationship(back_populates="approvisionnement")
    suivi_lignes: Mapped[list["AchatSuiviLigne"]] = relationship(
        back_populates="approvisionnement",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AchatSuiviLigne(Timestamps, Base):
    """Suivi réel d'achat, séparé du snapshot de comparaison (immuable)."""

    __tablename__ = "achat_suivi_lignes"
    __table_args__ = (
        UniqueConstraint(
            "chantier_id",
            "snapshot_token",
            "line_key",
            name="uq_achat_suivi_ligne",
        ),
        CheckConstraint(
            "quantite_reelle IS NULL OR (quantite_reelle >= 0 AND quantite_reelle <= 1000000)",
            name="ck_achat_quantite_reelle",
        ),
        CheckConstraint(
            "prix_reel IS NULL OR (prix_reel >= 0 AND prix_reel <= 1000000)",
            name="ck_achat_prix_reel",
        ),
        CheckConstraint("length(trim(line_key)) > 0", name="ck_achat_line_key"),
        CheckConstraint("length(trim(snapshot_token)) > 0", name="ck_achat_snapshot_token"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    chantier_id: Mapped[int] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"),
        index=True,
    )
    approvisionnement_id: Mapped[int] = mapped_column(
        ForeignKey("approvisionnements_retenus.id", ondelete="CASCADE"),
        index=True,
    )
    # Jeton de la solution retenue : {appro_id}:{strategy_key}:{chosen_at ISO}.
    # Empêche de rattacher le suivi d'une solution A aux lignes d'une solution B
    # (l'upsert réutilise la même ligne approvisionnement).
    snapshot_token: Mapped[str] = mapped_column(String(120))
    # Clé stable dans le snapshot : "{agency_id}:{product_id}".
    line_key: Mapped[str] = mapped_column(String(64))
    pris: Mapped[bool] = mapped_column(Boolean, default=False)
    # Nombre de packs réellement achetés (même sémantique que strategy.lines[].packs).
    quantite_reelle: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    # Prix réellement payé par pack / supplier_unit (même sémantique que pack_price).
    prix_reel: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    chantier: Mapped[Chantier] = relationship()
    approvisionnement: Mapped[ApprovisionnementRetenu] = relationship(
        back_populates="suivi_lignes"
    )
