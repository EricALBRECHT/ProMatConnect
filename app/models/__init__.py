from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Product(Timestamps, Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(80))
    reference_unit: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text)


class Supplier(Timestamps, Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Provenance dominante du fournisseur : demo | file | api
    source_type: Mapped[str] = mapped_column(String(20), default="demo")
    source_key: Mapped[str | None] = mapped_column(String(80), default=None)


class Agency(Timestamps, Base):
    __tablename__ = "agencies"
    __table_args__ = (
        UniqueConstraint("supplier_id", "name"),
        UniqueConstraint("id", "supplier_id"),
        UniqueConstraint("supplier_id", "external_id"),
        # NULL autorisé = agence non géolocalisée (exclue du comparateur distance/trajet).
        CheckConstraint("latitude IS NULL OR (latitude >= -90 AND latitude <= 90)"),
        CheckConstraint("longitude IS NULL OR (longitude >= -180 AND longitude <= 180)"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(80), default=None)
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(200))
    postal_code: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(100))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)


class SupplierProduct(Timestamps, Base):
    """Référence commerciale fournisseur — distincte du Product ProMatConnect.

    product_id nullable = référence importée non encore associée au catalogue PMC.
    Les offres liées à une référence non mappée sont exclues du comparateur.
    """

    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "supplier_reference"),
        UniqueConstraint("id", "supplier_id"),
        CheckConstraint("reference_quantity > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True, nullable=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_reference: Mapped[str] = mapped_column(String(60))
    designation: Mapped[str] = mapped_column(String(200))
    supplier_unit: Mapped[str] = mapped_column(String(40))
    # Nombre d'unités PMC dans UN conditionnement fournisseur.
    reference_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    brand: Mapped[str | None] = mapped_column(String(80), default=None)
    ean: Mapped[str | None] = mapped_column(String(32), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Offer(Timestamps, Base):
    """État courant d'une offre (supplier_product × agency) — upsert, pas d'historique infini."""

    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("supplier_product_id", "agency_id"),
        ForeignKeyConstraint(
            ["supplier_product_id", "supplier_id"],
            ["supplier_products.id", "supplier_products.supplier_id"],
        ),
        ForeignKeyConstraint(["agency_id", "supplier_id"], ["agencies.id", "agencies.supplier_id"]),
        CheckConstraint("price >= 0"),
        CheckConstraint("stock >= 0"),
        CheckConstraint("preparation_minutes >= 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    supplier_product_id: Mapped[int] = mapped_column(Integer, index=True)
    agency_id: Mapped[int] = mapped_column(Integer, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer)  # Conditionnements entiers.
    preparation_minutes: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    tax_basis: Mapped[str] = mapped_column(String(8), default="HT")
    source_type: Mapped[str] = mapped_column(String(20), default="demo")
    source_key: Mapped[str | None] = mapped_column(String(80), default=None)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class SupplierImport(Timestamps, Base):
    """Historique minimal des imports fichier (pas de payload brut stocké)."""

    __tablename__ = "supplier_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(80), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    supplier_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))  # preview_ok | imported | rejected
    rows: Mapped[int] = mapped_column(Integer, default=0)
    agencies: Mapped[int] = mapped_column(Integer, default=0)
    supplier_references: Mapped[int] = mapped_column(Integer, default=0)
    offers: Mapped[int] = mapped_column(Integer, default=0)
    mapped: Mapped[int] = mapped_column(Integer, default=0)
    unmapped: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)


from app.models.chantier import AchatSuiviLigne as AchatSuiviLigne  # noqa: E402
from app.models.chantier import ApprovisionnementRetenu as ApprovisionnementRetenu  # noqa: E402
from app.models.chantier import Chantier as Chantier  # noqa: E402
from app.models.chantier import ChantierMaterial as ChantierMaterial  # noqa: E402
