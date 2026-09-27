"""Import catalogue Brico Dépôt → SupplierProduct (idempotent, hors Offer live).

N'écrit jamais d'Offer. Ne touche pas au connecteur prix/stock live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.bricodepot.catalog import (
    VALIDATION_IMPORT_MAX,
    BricoDepotCatalogPipelineResult,
    BricoDepotCatalogService,
)
from app.connectors.bricodepot.catalog_dto import BricoDepotCatalogProduct
from app.connectors.bricodepot.connector import (
    CONNECTOR_KEY,
    SUPPLIER_NAME_ALIASES,
    is_brico_supplier_name,
)
from app.models import Offer, Supplier, SupplierImport, SupplierProduct

CANONICAL_SUPPLIER_NAME = "BRICO_DEPOT"
CATALOG_SOURCE_KEY = "api:BRICO_DEPOT:catalog"


@dataclass
class BricoDepotCatalogImportPreview:
    discovered: int = 0
    enriched: int = 0
    not_enriched: int = 0
    would_create: int = 0
    would_update: int = 0
    unchanged: int = 0
    mappings_preserved: int = 0
    errors: list[str] = field(default_factory=list)
    sample_products: list[BricoDepotCatalogProduct] = field(default_factory=list)
    products: list[BricoDepotCatalogProduct] = field(default_factory=list)
    missing_skus: list[str] = field(default_factory=list)
    sample_mode: str | None = None
    family_inventory: list[dict] = field(default_factory=list)
    family_by_sku: dict[str, str] = field(default_factory=dict)
    selection_counts: dict[str, int] = field(default_factory=dict)
    graphql_calls_sample: int = 0

    def to_dict(self) -> dict:
        return {
            "discovered": self.discovered,
            "enriched": self.enriched,
            "not_enriched": self.not_enriched,
            "would_create": self.would_create,
            "would_update": self.would_update,
            "unchanged": self.unchanged,
            "mappings_preserved": self.mappings_preserved,
            "errors": list(self.errors),
            "missing_skus": list(self.missing_skus),
            "sample_mode": self.sample_mode,
            "graphql_calls_sample": self.graphql_calls_sample,
            "selection_counts": dict(self.selection_counts),
            "family_inventory": list(self.family_inventory),
            "sample_products": [
                {
                    "supplier_reference": p.supplier_reference,
                    "name": p.name,
                    "ean": p.ean,
                    "sap_code": p.sap_code,
                    "product_url": p.product_url,
                    "packaging_label": p.packaging_label,
                    "content_net_value": str(p.content_net_value)
                    if p.content_net_value is not None
                    else None,
                    "content_net_unit": p.content_net_unit,
                    "category_path": p.category_path,
                    "image_url": p.image_url,
                }
                for p in self.sample_products
            ],
        }


@dataclass
class BricoDepotCatalogImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    mappings_preserved: int = 0
    skipped_not_enriched: int = 0
    offers_created: int = 0  # toujours 0 — garde-fou tests
    catalog_id: int | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "mappings_preserved": self.mappings_preserved,
            "skipped_not_enriched": self.skipped_not_enriched,
            "offers_created": self.offers_created,
            "catalog_id": self.catalog_id,
            "errors": list(self.errors),
        }


def resolve_brico_supplier(session: Session) -> Supplier:
    """Réutilise BRICO_DEPOT / BRICO DEPOT existant — pas de doublon."""
    rows = session.scalars(
        select(Supplier).where(Supplier.name.in_(tuple(SUPPLIER_NAME_ALIASES))).order_by(Supplier.id)
    ).all()
    if rows:
        for row in rows:
            if row.name == CANONICAL_SUPPLIER_NAME:
                return row
        return rows[0]
    supplier = Supplier(
        name=CANONICAL_SUPPLIER_NAME,
        source_type="api",
        source_key=CONNECTOR_KEY,
    )
    session.add(supplier)
    session.flush()
    return supplier


def ensure_catalog_import_row(
    session: Session, *, filename: str, row_count: int
) -> SupplierImport:
    existing = session.scalar(
        select(SupplierImport).where(SupplierImport.source_key == CATALOG_SOURCE_KEY)
    )
    if existing is None:
        catalog = SupplierImport(
            source_key=CATALOG_SOURCE_KEY,
            filename=filename[:200],
            supplier_name=CANONICAL_SUPPLIER_NAME,
            status="imported",
            active=True,
            tax_basis=None,
            rows=row_count,
            rows_with_price=0,
            rows_without_price=row_count,
            agencies=0,
            supplier_references=row_count,
            offers=0,
            mapped=0,
            unmapped=row_count,
            error_count=0,
        )
        session.add(catalog)
        session.flush()
        return catalog
    existing.filename = filename[:200]
    existing.status = "imported"
    existing.active = True
    existing.rows = row_count
    existing.rows_without_price = row_count
    existing.supplier_references = row_count
    session.flush()
    return existing


def _supplier_unit_from_catalog(product: BricoDepotCatalogProduct) -> str:
    label = (product.packaging_label or "").strip()
    if label:
        return label[:40]
    return "unité"


def _norm_str(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _image_url_for_store(product: BricoDepotCatalogProduct) -> str | None:
    url = _norm_str(product.image_url)
    if url is None:
        return None
    return url[:500]


def proposed_catalog_fields(
    product: BricoDepotCatalogProduct, *, correction_source: str | None
) -> dict[str, Any]:
    """Champs catalogue que l'import est autorisé à écrire.

    correction_source=manual : pas de supplier_unit / conditionnement.
    manual | exact_rule : ne pas réécrire correction_source → import.
    """
    from app.models.product_mapping import CORRECTION_SOURCES_PROTECTED

    designation = _norm_str(product.name) or product.supplier_reference
    fields: dict[str, Any] = {
        "designation": designation[:200],
        "active": True,
    }
    # ean / brand / image : uniquement si fournis par le catalogue
    ean = _norm_str(product.ean)
    if ean is not None:
        fields["ean"] = ean[:32]
    brand = _norm_str(product.brand)
    if brand is not None:
        fields["brand"] = brand[:80]
    image = _image_url_for_store(product)
    if image is not None:
        fields["image_url"] = image
    if correction_source != "manual":
        fields["supplier_unit"] = _supplier_unit_from_catalog(product)
    if correction_source not in CORRECTION_SOURCES_PROTECTED:
        fields["correction_source"] = "import"
    return fields


def current_catalog_fields(sp: SupplierProduct, keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        value = getattr(sp, key)
        if isinstance(value, str):
            out[key] = _norm_str(value)
        else:
            out[key] = value
    return out


def catalog_field_diff(
    sp: SupplierProduct, product: BricoDepotCatalogProduct
) -> dict[str, tuple[Any, Any]]:
    """Diff des champs importables (old, new). Vide = unchanged."""
    proposed = proposed_catalog_fields(
        product, correction_source=sp.correction_source
    )
    current = current_catalog_fields(sp, list(proposed.keys()))
    diff: dict[str, tuple[Any, Any]] = {}
    for key, new_val in proposed.items():
        old_val = current.get(key)
        if old_val != new_val:
            diff[key] = (old_val, new_val)
    return diff


def classify_catalog_action(
    sp: SupplierProduct | None,
    product: BricoDepotCatalogProduct,
    *,
    missing: bool,
) -> str:
    """created | updated | unchanged | skipped."""
    enriched = bool(product.name or product.ean or product.image_url)
    if sp is None:
        if missing or not enriched:
            return "skipped"
        return "created"
    if missing and not enriched:
        return "unchanged"
    if not catalog_field_diff(sp, product):
        return "unchanged"
    return "updated"


def upsert_supplier_product(
    session: Session,
    *,
    supplier: Supplier,
    product: BricoDepotCatalogProduct,
    catalog_id: int | None,
) -> tuple[str, bool]:
    """Crée / met à jour / no-op. Retourne (action, mapping_preserved).

    action: created | updated | unchanged | skipped
    Ne crée aucune Offer. Préserve product_id et corrections manuelles.
    """
    sp = session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier.id,
            SupplierProduct.supplier_reference == product.supplier_reference,
        )
    )
    enriched = bool(product.name or product.ean or product.image_url)
    missing = not enriched

    if sp is None:
        if missing:
            return "skipped", False
        designation = (_norm_str(product.name) or product.supplier_reference)[:200]
        unit = _supplier_unit_from_catalog(product)
        sp = SupplierProduct(
            product_id=None,
            supplier_id=supplier.id,
            supplier_reference=product.supplier_reference[:60],
            designation=designation,
            supplier_unit=unit,
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit=None,
            brand=_norm_str(product.brand),
            ean=_norm_str(product.ean),
            image_url=_image_url_for_store(product),
            active=True,
            introduced_by_catalog_id=catalog_id,
            correction_source="import",
        )
        session.add(sp)
        session.flush()
        return "created", False

    mapping_preserved = sp.product_id is not None
    action = classify_catalog_action(sp, product, missing=missing)
    if action == "unchanged":
        # Lier au catalogue si pas encore fait, sans toucher aux champs métier.
        if catalog_id is not None and sp.introduced_by_catalog_id is None:
            sp.introduced_by_catalog_id = catalog_id
            session.flush()
            # liaison catalogue seule → toujours "unchanged" métier
        return "unchanged", mapping_preserved

    if action == "skipped":
        return "skipped", mapping_preserved

    # updated
    proposed = proposed_catalog_fields(
        product, correction_source=sp.correction_source
    )
    for key, value in proposed.items():
        setattr(sp, key, value)
    if catalog_id is not None and sp.introduced_by_catalog_id is None:
        sp.introduced_by_catalog_id = catalog_id
    session.flush()
    return "updated", mapping_preserved


class BricoDepotCatalogImportService:
    def __init__(
        self,
        session: Session,
        *,
        catalog_service: BricoDepotCatalogService | None = None,
    ):
        self.session = session
        self.catalog_service = catalog_service or BricoDepotCatalogService()

    def preview(
        self,
        *,
        limit: int = VALIDATION_IMPORT_MAX,
        sample_diverse: bool = False,
    ) -> BricoDepotCatalogImportPreview:
        limit = self._clamp_limit(limit)
        pipeline = self.catalog_service.build_catalog(
            limit=limit, sample_diverse=sample_diverse
        )
        return self.preview_from_pipeline(pipeline)

    def preview_from_pipeline(
        self, pipeline: BricoDepotCatalogPipelineResult
    ) -> BricoDepotCatalogImportPreview:
        preview = BricoDepotCatalogImportPreview(
            discovered=len(pipeline.discovered),
            enriched=pipeline.enriched_count,
            not_enriched=len(pipeline.missing_skus),
            errors=list(pipeline.errors),
            products=list(pipeline.products),
            sample_products=list(pipeline.products[:5]),
            missing_skus=list(pipeline.missing_skus),
            sample_mode=pipeline.sample_mode,
            family_inventory=list(pipeline.family_inventory),
            family_by_sku=dict(pipeline.family_by_sku),
            selection_counts=dict(pipeline.selection_counts),
            graphql_calls_sample=pipeline.graphql_calls_sample,
        )
        supplier = self.session.scalars(
            select(Supplier).where(Supplier.name.in_(tuple(SUPPLIER_NAME_ALIASES)))
        ).first()
        missing = set(pipeline.missing_skus)
        if supplier is None:
            preview.would_create = sum(
                1 for p in pipeline.products if p.supplier_reference not in missing
            )
            return preview

        existing_refs = {
            sp.supplier_reference: sp
            for sp in self.session.scalars(
                select(SupplierProduct).where(SupplierProduct.supplier_id == supplier.id)
            ).all()
        }
        for product in pipeline.products:
            sp = existing_refs.get(product.supplier_reference)
            is_missing = product.supplier_reference in missing
            action = classify_catalog_action(sp, product, missing=is_missing)
            if action == "created":
                preview.would_create += 1
            elif action == "updated":
                preview.would_update += 1
                if sp is not None and sp.product_id is not None:
                    preview.mappings_preserved += 1
            elif action == "unchanged":
                preview.unchanged += 1
                if sp is not None and sp.product_id is not None:
                    preview.mappings_preserved += 1
            # skipped : non compté dans create/update/unchanged
        return preview

    def apply(
        self,
        *,
        limit: int = VALIDATION_IMPORT_MAX,
        products: list[BricoDepotCatalogProduct] | None = None,
        missing_skus: list[str] | None = None,
        sample_diverse: bool = False,
    ) -> BricoDepotCatalogImportResult:
        """Écriture DB explicite uniquement — jamais appelée au démarrage app."""
        limit = self._clamp_limit(limit)
        result = BricoDepotCatalogImportResult()
        if products is None:
            pipeline = self.catalog_service.build_catalog(
                limit=limit, sample_diverse=sample_diverse
            )
            products = pipeline.products
            missing_skus = pipeline.missing_skus
            result.errors.extend(pipeline.errors)

        supplier = resolve_brico_supplier(self.session)
        assert is_brico_supplier_name(supplier.name)

        to_write = [p for p in products[:limit]]
        tag = "diverse-sample" if sample_diverse else "sitemap-catalog"
        catalog = ensure_catalog_import_row(
            self.session,
            filename=f"bricodepot-{tag}-limit{limit}",
            row_count=len(to_write),
        )
        result.catalog_id = catalog.id

        for product in to_write:
            try:
                action, mapping_preserved = upsert_supplier_product(
                    self.session,
                    supplier=supplier,
                    product=product,
                    catalog_id=catalog.id,
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{product.supplier_reference}: {exc}")
                continue
            if action == "created":
                result.created += 1
            elif action == "updated":
                result.updated += 1
                if mapping_preserved:
                    result.mappings_preserved += 1
            elif action == "unchanged":
                result.unchanged += 1
                if mapping_preserved:
                    result.mappings_preserved += 1
            else:
                result.skipped_not_enriched += 1

        result.offers_created = 0
        self.session.flush()
        return result

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        if limit < 1:
            raise ValueError("limit doit être >= 1")
        if limit > VALIDATION_IMPORT_MAX:
            raise ValueError(
                f"limit validation max = {VALIDATION_IMPORT_MAX} (reçu {limit})"
            )
        return int(limit)
