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


class Agency(Timestamps, Base):
    __tablename__ = "agencies"
    __table_args__ = (
        UniqueConstraint("supplier_id", "name"),
        UniqueConstraint("id", "supplier_id"),
        CheckConstraint("latitude >= -90 AND latitude <= 90"),
        CheckConstraint("longitude >= -180 AND longitude <= 180"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(200))
    postal_code: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(100))
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))


class SupplierProduct(Timestamps, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "supplier_reference"),
        UniqueConstraint("id", "supplier_id"),
        CheckConstraint("reference_quantity > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_reference: Mapped[str] = mapped_column(String(60))
    designation: Mapped[str] = mapped_column(String(200))
    supplier_unit: Mapped[str] = mapped_column(String(40))
    # Nombre d'unités PMC dans UN conditionnement fournisseur.
    reference_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Offer(Timestamps, Base):
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
