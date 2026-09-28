"""Normalisation / mapping produit — catégories, features, propositions.

V1 : famille PLAQUE_PLATRE uniquement.
Aucune écriture automatique de SupplierProduct.product_id.
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# Statuts de proposition (jamais d'auto-accept en V1)
PROPOSAL_EXACT = "exact"
PROPOSAL_HIGH = "high"
PROPOSAL_REVIEW = "review"
PROPOSAL_UNMAPPED = "unmapped"

CATEGORY_PLAQUE_PLATRE = "PLAQUE_PLATRE"
EXTRACTOR_VERSION_PLAQUE_V1 = "plaque_platre.v1"
ALGORITHM_VERSION_PLAQUE_V1 = "plaque_match.v1"

CATEGORY_OSSATURE_PLACO = "OSSATURE_PLACO"
EXTRACTOR_VERSION_OSSATURE_V1 = "ossature_placo.v1"
ALGORITHM_VERSION_OSSATURE_V1 = "ossature_match.v1"

CATEGORY_VIS_PLACO = "VIS_PLACO"
EXTRACTOR_VERSION_VIS_V1 = "vis_placo.v1"
ALGORITHM_VERSION_VIS_V1 = "vis_match.v1"

CATEGORY_VIS_AGGLO = "VIS_AGGLO"
EXTRACTOR_VERSION_VIS_AGGLO_V1 = "vis_agglo.v1"
ALGORITHM_VERSION_VIS_AGGLO_V1 = "vis_agglo_match.v1"

CATEGORY_VIS_BOIS = "VIS_BOIS"
EXTRACTOR_VERSION_VIS_BOIS_V1 = "vis_bois.v1"
ALGORITHM_VERSION_VIS_BOIS_V1 = "vis_bois_match.v1"

CATEGORY_VIS_MULTI = "VIS_MULTI"
EXTRACTOR_VERSION_VIS_MULTI_V1 = "vis_multi.v1"
ALGORITHM_VERSION_VIS_MULTI_V1 = "vis_multi_match.v1"

CATEGORY_CHEVILLE_METAL = "CHEVILLE_METAL"
EXTRACTOR_VERSION_CHEVILLE_METAL_V1 = "cheville_metal.v1"
ALGORITHM_VERSION_CHEVILLE_METAL_V1 = "cheville_metal_match.v1"

CATEGORY_CORNIERE_PVC = "CORNIERE_PVC"
EXTRACTOR_VERSION_CORNIERE_PVC_V1 = "corniere_pvc.v1"
ALGORITHM_VERSION_CORNIERE_PVC_V1 = "corniere_pvc_match.v1"

CATEGORY_ROND_ACIER = "ROND_ACIER"
EXTRACTOR_VERSION_ROND_ACIER_V1 = "rond_acier.v1"
ALGORITHM_VERSION_ROND_ACIER_V1 = "rond_acier_match.v1"

CATEGORY_TUBE_ROND_ACIER = "TUBE_ROND_ACIER"
EXTRACTOR_VERSION_TUBE_ROND_ACIER_V1 = "tube_rond_acier.v1"
ALGORITHM_VERSION_TUBE_ROND_ACIER_V1 = "tube_rond_acier_match.v1"

CATEGORY_FER_BETON = "FER_BETON"
EXTRACTOR_VERSION_FER_BETON_V1 = "fer_beton.v1"
ALGORITHM_VERSION_FER_BETON_V1 = "fer_beton_match.v1"

CATEGORY_PANNEAU_MDF = "PANNEAU_MDF"
EXTRACTOR_VERSION_PANNEAU_MDF_V1 = "panneau_mdf.v1"
ALGORITHM_VERSION_PANNEAU_MDF_V1 = "panneau_mdf_match.v1"

KIND_RAIL = "RAIL"
KIND_MONTANT = "MONTANT"
KIND_FOURRURE = "FOURRURE"
KIND_CORNIERE_PVC = "CORNIERE_PVC"
KIND_ROND_ACIER = "ROND_ACIER"
KIND_TUBE_ROND_ACIER = "TUBE_ROND_ACIER"
KIND_FER_BETON = "FER_BETON"

# Traçabilité SupplierProduct.correction_source (VARCHAR(20))
CORRECTION_SOURCE_IMPORT = "import"
CORRECTION_SOURCE_MANUAL = "manual"
CORRECTION_SOURCE_EXACT_RULE = "exact_rule"
CORRECTION_SOURCE_SPECIFIC = "specific"
CORRECTION_SOURCE_EAN = "ean"
CORRECTION_SOURCE_EQUIV = "equiv"
CORRECTION_SOURCES_PROTECTED = frozenset(
    {
        CORRECTION_SOURCE_MANUAL,
        CORRECTION_SOURCE_EXACT_RULE,
        CORRECTION_SOURCE_SPECIFIC,
        CORRECTION_SOURCE_EAN,
        CORRECTION_SOURCE_EQUIV,
    }
)


class ProductCategory(Base):
    """Catégorie produit PMC — hiérarchie via parent_id (nullable = racine)."""

    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("code", name="uq_product_categories_code"),
        Index("ix_product_categories_parent", "parent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_categories.id"), nullable=True, default=None
    )
    reference_unit_default: Mapped[str | None] = mapped_column(String(30), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(40), default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProductAttributeDef(Base):
    """Définition d'attribut pour une catégorie (schéma déclaratif)."""

    __tablename__ = "product_attribute_defs"
    __table_args__ = (
        UniqueConstraint("category_id", "key", name="uq_product_attr_def_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("product_categories.id"), index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    data_type: Mapped[str] = mapped_column(String(20))  # int|decimal|bool|enum|text
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    enum_values: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    # identity = obligatoire pour EXACT ; optional = conflit si les deux côtés sont connus
    match_role: Mapped[str] = mapped_column(String(20), default="optional")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SupplierProductFeature(Base):
    """Caractéristiques extraites d'un SupplierProduct (une ligne / SP)."""

    __tablename__ = "supplier_product_features"
    __table_args__ = (
        UniqueConstraint("supplier_product_id", name="uq_sp_features_sp"),
        Index("ix_sp_features_category", "category_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_product_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_products.id"), index=True
    )
    category_code: Mapped[str] = mapped_column(String(64))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    extractor_version: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProductMappingProposal(Base):
    """Proposition de mapping — ne modifie jamais SupplierProduct.product_id."""

    __tablename__ = "product_mapping_proposals"
    __table_args__ = (
        Index("ix_mapping_proposals_sp", "supplier_product_id"),
        Index("ix_mapping_proposals_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_product_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_products.id"), index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True, default=None, index=True
    )
    status: Mapped[str] = mapped_column(String(20))
    score: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
