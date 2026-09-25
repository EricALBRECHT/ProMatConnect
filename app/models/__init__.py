from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
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
    """Besoin technique normalisé ProMatConnect (distinct des références commerciales).

    Les Product PMC000x démo restent en place (is_legacy) pour l'historique.
    Les caractéristiques évolutives vont dans `attributes` (JSON), pas en colonnes ad hoc.
    """

    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(80))
    subcategory: Mapped[str | None] = mapped_column(String(80), default=None)
    reference_unit: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text)
    # Caractéristiques techniques optionnelles (dimensions, lambda, R, finish…).
    attributes: Mapped[dict | None] = mapped_column(JSON, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False)


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

    Conditionnement :
    - packaging_quantity : nb d'unités fournisseur dans UN pack vendu (souvent 1)
    - reference_quantity : nb d'unités de référence PMC dans UN pack
    - reference_unit : unité PMC (m, m2, piece…) ; défaut = Product.reference_unit
    """

    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "supplier_reference"),
        UniqueConstraint("id", "supplier_id"),
        CheckConstraint("reference_quantity > 0"),
        CheckConstraint("packaging_quantity > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True, nullable=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_reference: Mapped[str] = mapped_column(String(60))
    designation: Mapped[str] = mapped_column(String(200))
    supplier_unit: Mapped[str] = mapped_column(String(40))
    # Nombre d'unités PMC dans UN conditionnement fournisseur.
    reference_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    packaging_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"))
    reference_unit: Mapped[str | None] = mapped_column(String(30), default=None)
    brand: Mapped[str | None] = mapped_column(String(80), default=None)
    ean: Mapped[str | None] = mapped_column(String(32), default=None)
    # URL http(s) distante uniquement — jamais téléchargée côté serveur.
    image_url: Mapped[str | None] = mapped_column(String(500), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Lien souple (entier) — pas de FK dure pour garder create_all additif
    # sur schémas préexistants sans table supplier_imports.
    introduced_by_catalog_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None, index=True
    )
    # None | import | manual — une correction manuelle n'est pas écrasée par réimport CSV.
    correction_source: Mapped[str | None] = mapped_column(String(20), default=None)


class SupplierImport(Timestamps, Base):
    """Catalogue / import fichier identifiable (désactivable, suppression contrôlée)."""

    __tablename__ = "supplier_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(80), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    supplier_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))  # imported | deactivated
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    tax_basis: Mapped[str | None] = mapped_column(String(8), default=None)  # HT | TTC | MIXED
    rows: Mapped[int] = mapped_column(Integer, default=0)
    rows_with_price: Mapped[int] = mapped_column(Integer, default=0)
    rows_without_price: Mapped[int] = mapped_column(Integer, default=0)
    agencies: Mapped[int] = mapped_column(Integer, default=0)
    supplier_references: Mapped[int] = mapped_column(Integer, default=0)
    offers: Mapped[int] = mapped_column(Integer, default=0)
    mapped: Mapped[int] = mapped_column(Integer, default=0)
    unmapped: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)


class Offer(Timestamps, Base):
    """État courant d'une offre (supplier_product × agency) — upsert, pas d'historique infini.

    catalog_id lie l'offre à un SupplierImport pour désactivation/suppression sûres.
    Les offres démo (catalog_id NULL) restent toujours éligibles si actives.
    """

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
    catalog_id: Mapped[int | None] = mapped_column(
        Integer, index=True, nullable=True, default=None
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer)  # Conditionnements entiers.
    preparation_minutes: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    tax_basis: Mapped[str] = mapped_column(String(8), default="HT")
    # Taux de TVA en % (ex. 20.00). NULL = conversion HT↔TTC impossible.
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True, default=None)
    source_type: Mapped[str] = mapped_column(String(20), default="demo")
    source_key: Mapped[str | None] = mapped_column(String(80), default=None)
    source_url: Mapped[str | None] = mapped_column(String(500), default=None)
    seller: Mapped[str | None] = mapped_column(String(120), default=None)
    verification_status: Mapped[str | None] = mapped_column(String(40), default=None)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


from app.models.chantier import AchatSuiviLigne as AchatSuiviLigne  # noqa: E402
from app.models.chantier import ApprovisionnementRetenu as ApprovisionnementRetenu  # noqa: E402
from app.models.chantier import Chantier as Chantier  # noqa: E402
from app.models.chantier import ChantierMaterial as ChantierMaterial  # noqa: E402
