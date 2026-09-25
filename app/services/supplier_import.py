"""Preview + import CSV + mapping SupplierProduct ↔ Product + catalogues."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.connectors.file_csv import FileSupplierConnector
from app.connectors.normalized import NormalizedOffer
from app.models import Agency, Offer, Product, Supplier, SupplierImport, SupplierProduct
from app.services.units import normalize_unit, units_compatible


@dataclass
class UnmappedProductInfo:
    external_reference: str
    name: str
    supplier: str
    image_url: str | None = None
    brand: str | None = None
    supplier_unit: str | None = None
    packaging_quantity: str | None = None
    reference_unit: str | None = None
    reference_quantity: str | None = None
    price: str | None = None
    tax_basis: str | None = None


@dataclass
class ImportPreviewReport:
    valid: bool
    rows: int = 0
    rows_with_price: int = 0
    rows_without_price: int = 0
    rows_ht: int = 0
    rows_ttc: int = 0
    agencies: int = 0
    supplier_references: int = 0
    offers: int = 0
    mapped: int = 0
    unmapped: int = 0
    tax_bases: list[str] = field(default_factory=list)
    tax_basis: str | None = None
    mixed_tax_basis: bool = False
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    unmapped_products: list[UnmappedProductInfo] = field(default_factory=list)
    mapping_rows: list[dict] = field(default_factory=list)
    suppliers: list[str] = field(default_factory=list)
    sample_rows: list[dict] = field(default_factory=list)
    detected_columns: list[str] = field(default_factory=list)
    delimiter: str | None = None
    filename: str = ""
    source_key: str | None = None

    def to_dict(self) -> dict:
        warning_msgs = [
            w.get("message", str(w)) if isinstance(w, dict) else str(w)
            for w in self.warnings
        ]
        return {
            "valid": self.valid,
            "rows": self.rows,
            "rows_with_price": self.rows_with_price,
            "rows_without_price": self.rows_without_price,
            "rows_ht": self.rows_ht,
            "rows_ttc": self.rows_ttc,
            "agencies": self.agencies,
            "supplier_references": self.supplier_references,
            "offers": self.offers,
            "mapped": self.mapped,
            "unmapped": self.unmapped,
            "tax_bases": self.tax_bases,
            "tax_basis": self.tax_basis,
            "mixed_tax_basis": self.mixed_tax_basis,
            "errors": self.errors,
            "warnings": warning_msgs,
            "infos": list(self.infos),
            "unmapped_products": [
                {
                    "external_reference": u.external_reference,
                    "name": u.name,
                    "supplier": u.supplier,
                    "image_url": u.image_url,
                    "brand": u.brand,
                    "supplier_unit": u.supplier_unit,
                    "packaging_quantity": u.packaging_quantity,
                    "reference_unit": u.reference_unit,
                    "reference_quantity": u.reference_quantity,
                    "price": u.price,
                    "tax_basis": u.tax_basis,
                    "status": "Non associé",
                }
                for u in self.unmapped_products
            ],
            "mapping_rows": self.mapping_rows,
            "suppliers": self.suppliers,
            "supplier_name": ", ".join(self.suppliers) if self.suppliers else "",
            "sample_rows": self.sample_rows,
            "detected_columns": self.detected_columns,
            "delimiter": self.delimiter,
            "filename": self.filename,
            "source_key": self.source_key,
        }


@dataclass
class ImportCommitResult:
    valid: bool
    source_key: str
    catalog_id: int | None = None
    rows: int = 0
    rows_with_price: int = 0
    rows_without_price: int = 0
    agencies: int = 0
    supplier_references: int = 0
    offers: int = 0
    mapped: int = 0
    unmapped: int = 0
    tax_basis: str | None = None
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "source_key": self.source_key,
            "catalog_id": self.catalog_id,
            "rows": self.rows,
            "rows_with_price": self.rows_with_price,
            "rows_without_price": self.rows_without_price,
            "agencies": self.agencies,
            "supplier_references": self.supplier_references,
            "offers": self.offers,
            "mapped": self.mapped,
            "unmapped": self.unmapped,
            "tax_basis": self.tax_basis,
            "errors": self.errors,
        }


class SupplierImportService:
    def __init__(self, session: Session):
        self.session = session

    def preview(self, data: bytes, filename: str | None) -> ImportPreviewReport:
        try:
            safe_name = FileSupplierConnector.validate_filename(filename)
        except ValueError as error:
            return ImportPreviewReport(
                valid=False,
                errors=[{"line": 0, "code": "filename", "message": str(error)}],
                filename=filename or "",
            )

        connector, errors, raw_warnings = FileSupplierConnector.parse_upload(
            data, filename=safe_name
        )
        offers = connector.normalized_offers
        detected = list(getattr(connector, "_detected_columns", []) or [])
        delimiter = getattr(connector, "_delimiter", None)
        infos, warnings = self._summarize_warnings(raw_warnings, offers)
        if errors:
            return ImportPreviewReport(
                valid=False,
                rows=len(offers),
                errors=errors,
                warnings=warnings,
                infos=infos,
                detected_columns=detected,
                delimiter=delimiter,
                filename=safe_name,
            )

        mapping = self._resolve_mappings(offers)
        product_names = self._product_names(
            {pid for pid in mapping.values() if pid is not None}
        )
        mapping_rows = self._build_mapping_rows(offers, mapping, product_names)
        mapped_refs = {k for k, v in mapping.items() if v is not None}
        unmapped_infos = [
            UnmappedProductInfo(
                external_reference=row["external_reference"],
                name=row["name"],
                supplier=row["supplier"],
                image_url=row.get("image_url"),
                brand=row.get("brand"),
                supplier_unit=row.get("supplier_unit"),
                packaging_quantity=row.get("packaging_quantity"),
                reference_unit=row.get("reference_unit"),
                reference_quantity=row.get("reference_quantity"),
                price=row.get("price"),
                tax_basis=row.get("tax_basis"),
            )
            for row in mapping_rows
            if row.get("product_id") is None
        ]

        agencies = {
            (o.supplier, o.agency.external_id)
            for o in offers
            if (o.agency.external_id or "").strip()
        }
        refs = {(o.supplier, o.product.external_reference) for o in offers}
        suppliers = sorted({o.supplier for o in offers})
        with_price = [o for o in offers if o.price is not None]
        without_price = len(offers) - len(with_price)
        tax_bases = sorted({o.tax_basis for o in offers})
        tax_basis = self._catalog_tax_basis(offers) if offers else None
        rows_ht = sum(1 for o in offers if o.tax_basis == "HT")
        rows_ttc = sum(1 for o in offers if o.tax_basis == "TTC")
        sample_rows = [
            {
                "agency_code": o.agency.external_id or "",
                "supplier_reference": o.product.external_reference,
                "product_name": o.product.name,
                "brand": o.product.brand,
                "price": str(o.price) if o.price is not None else None,
                "tax_basis": o.tax_basis,
                "vat_rate": str(o.vat_rate) if o.vat_rate is not None else None,
                "supplier_unit": o.product.supplier_unit,
                "reference_unit": o.product.reference_unit,
                "reference_quantity": str(o.product.reference_quantity)
                if o.product.reference_quantity is not None
                else None,
                "image_url": o.product.image_url,
                "source_url": o.source_url,
                "product_id": mapping.get((o.supplier, o.product.external_reference)),
                "supplier": o.supplier,
            }
            for o in offers[:12]
        ]

        return ImportPreviewReport(
            valid=True,
            rows=len(offers),
            rows_with_price=len(with_price),
            rows_without_price=without_price,
            rows_ht=rows_ht,
            rows_ttc=rows_ttc,
            agencies=len(agencies),
            supplier_references=len(refs),
            offers=len(with_price),
            mapped=len({k for k in refs if k in mapped_refs}),
            unmapped=len(refs) - len({k for k in refs if k in mapped_refs}),
            tax_bases=tax_bases,
            tax_basis=tax_basis,
            mixed_tax_basis=len(tax_bases) > 1,
            errors=[],
            warnings=warnings,
            infos=infos,
            unmapped_products=unmapped_infos,
            mapping_rows=mapping_rows,
            suppliers=suppliers,
            sample_rows=sample_rows,
            detected_columns=detected,
            delimiter=delimiter,
            filename=safe_name,
        )

    def import_file(
        self,
        data: bytes,
        filename: str | None,
        *,
        mappings: list[dict] | None = None,
    ) -> ImportCommitResult:
        report = self.preview(data, filename)
        if not report.valid:
            return ImportCommitResult(
                valid=False, source_key="", errors=report.errors, rows=report.rows
            )

        connector, errors, _warnings = FileSupplierConnector.parse_upload(
            data, filename=report.filename
        )
        if errors:
            return ImportCommitResult(valid=False, source_key="", errors=errors)

        offers = connector.normalized_offers
        source_key = self._next_source_key()
        resolved = self._resolve_mappings(offers)
        explicit_keys = self._apply_explicit_mappings(resolved, mappings, offers)
        # Recompte mapped/unmapped après mappings explicites.
        refs = {(o.supplier, o.product.external_reference) for o in offers}
        mapped_n = sum(1 for k in refs if resolved.get(k) is not None)
        unmapped_n = len(refs) - mapped_n
        tax_basis = self._catalog_tax_basis(offers)

        catalog = SupplierImport(
            source_key=source_key,
            filename=report.filename,
            supplier_name=", ".join(report.suppliers)[:100],
            status="imported",
            active=True,
            tax_basis=tax_basis,
            rows=report.rows,
            rows_with_price=report.rows_with_price,
            rows_without_price=report.rows_without_price,
            agencies=report.agencies,
            supplier_references=report.supplier_references,
            offers=report.offers,
            mapped=mapped_n,
            unmapped=unmapped_n,
            error_count=0,
        )
        try:
            self.session.add(catalog)
            self.session.flush()
            for offer in offers:
                self._upsert_row(
                    offer,
                    resolved,
                    source_key,
                    catalog.id,
                    explicit_keys=explicit_keys,
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return ImportCommitResult(
            valid=True,
            source_key=source_key,
            catalog_id=catalog.id,
            rows=report.rows,
            rows_with_price=report.rows_with_price,
            rows_without_price=report.rows_without_price,
            agencies=report.agencies,
            supplier_references=report.supplier_references,
            offers=report.offers,
            mapped=mapped_n,
            unmapped=unmapped_n,
            tax_basis=tax_basis,
        )

    def list_imports(self, limit: int = 50) -> list[SupplierImport]:
        return list(
            self.session.scalars(
                select(SupplierImport).order_by(SupplierImport.created_at.desc()).limit(limit)
            )
        )

    def list_catalogs(self) -> list[dict]:
        catalogs = []
        for row in self.list_imports(100):
            live_offers = int(
                self.session.scalar(
                    select(func.count()).select_from(Offer).where(Offer.catalog_id == row.id)
                )
                or 0
            )
            unmapped = self._catalog_unmapped_count(row.id)
            can_delete, delete_reason = self._deletion_policy(row, live_offers)
            catalogs.append(
                {
                    "id": row.id,
                    "source_key": row.source_key,
                    "filename": row.filename,
                    "supplier_name": row.supplier_name,
                    "source_type": "file",
                    "status": "active" if row.active else "deactivated",
                    "active": bool(row.active),
                    "tax_basis": row.tax_basis,
                    "rows": row.rows,
                    "offers": live_offers,
                    "offers_at_import": row.offers,
                    "supplier_references": row.supplier_references,
                    "mapped": row.mapped,
                    "unmapped": unmapped,
                    "can_delete": can_delete,
                    "delete_blocked_reason": delete_reason,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        return catalogs

    def list_sources(self) -> list[dict]:
        rows = []
        for supplier in self.session.scalars(select(Supplier).order_by(Supplier.name)):
            last = self.session.scalar(
                select(func.max(Offer.updated_at)).where(Offer.supplier_id == supplier.id)
            )
            offer_count = self.session.scalar(
                select(func.count()).select_from(Offer).where(Offer.supplier_id == supplier.id)
            )
            rows.append(
                {
                    "name": supplier.name,
                    "source_type": supplier.source_type or "demo",
                    "source_key": supplier.source_key,
                    "status": "active",
                    "offers": int(offer_count or 0),
                    "updated_at": last.isoformat() if last else None,
                }
            )
        return rows

    def list_catalog_mappings(self, catalog_id: int) -> dict:
        catalog = self.session.get(SupplierImport, catalog_id)
        if catalog is None:
            raise LookupError("Catalogue introuvable.")
        sps = self._catalog_supplier_products(catalog_id)
        product_ids = {sp.product_id for sp in sps if sp.product_id is not None}
        names = self._product_names(product_ids)
        codes = self._product_codes(product_ids)
        product_units = self._product_units(product_ids)
        product_attrs = self._product_attributes(product_ids)
        offers_by_sp = self._latest_offers_for_sps([sp.id for sp in sps])
        items = []
        for sp in sps:
            supplier = self.session.get(Supplier, sp.supplier_id)
            p_unit = product_units.get(sp.product_id) if sp.product_id else None
            compatible = (
                units_compatible(p_unit, sp.reference_unit) if sp.product_id else None
            )
            offer = offers_by_sp.get(sp.id)
            items.append(
                {
                    "supplier_product_id": sp.id,
                    "supplier": supplier.name if supplier else "",
                    "external_reference": sp.supplier_reference,
                    "name": sp.designation,
                    "brand": sp.brand,
                    "supplier_unit": sp.supplier_unit,
                    "packaging_quantity": str(sp.packaging_quantity)
                    if sp.packaging_quantity is not None
                    else None,
                    "reference_unit": sp.reference_unit,
                    "reference_quantity": str(sp.reference_quantity)
                    if sp.reference_quantity is not None
                    else None,
                    "product_id": sp.product_id,
                    "product_code": codes.get(sp.product_id) if sp.product_id else None,
                    "product_name": names.get(sp.product_id) if sp.product_id else None,
                    "product_reference_unit": p_unit,
                    "product_attributes": product_attrs.get(sp.product_id) if sp.product_id else None,
                    "mapped": sp.product_id is not None,
                    "unit_compatible": compatible,
                    "unit_anomaly": bool(sp.product_id and compatible is False),
                    "correction_source": sp.correction_source,
                    "updated_at": sp.updated_at.isoformat() if sp.updated_at else None,
                    "price": str(offer.price) if offer else None,
                    "tax_basis": offer.tax_basis if offer else None,
                    "vat_rate": str(offer.vat_rate) if offer and offer.vat_rate is not None else None,
                    "image_url": sp.image_url,
                }
            )
        return {
            "catalog_id": catalog.id,
            "source_key": catalog.source_key,
            "filename": catalog.filename,
            "items": items,
            "mapped": sum(1 for i in items if i["mapped"]),
            "unmapped": sum(1 for i in items if not i["mapped"]),
        }

    def set_supplier_product_mapping(
        self, supplier_product_id: int, product_id: int | None
    ) -> dict:
        """Association explicite uniquement — jamais de mapping automatique."""
        sp = self.session.get(SupplierProduct, supplier_product_id)
        if sp is None:
            raise LookupError("Référence fournisseur introuvable.")
        if product_id is not None:
            product = self.session.get(Product, product_id)
            if product is None:
                raise LookupError("Produit ProMatConnect introuvable.")
            sp.product_id = product_id
            product_code = product.code
            product_name = product.name
            product_unit = product.reference_unit
        else:
            sp.product_id = None
            product_code = None
            product_name = None
            product_unit = None
        sp.correction_source = "manual"
        self.session.commit()
        compatible = (
            units_compatible(product_unit, sp.reference_unit) if product_id else None
        )
        return {
            "supplier_product_id": sp.id,
            "product_id": sp.product_id,
            "product_code": product_code,
            "product_name": product_name,
            "product_reference_unit": product_unit,
            "mapped": sp.product_id is not None,
            "unit_compatible": compatible,
            "unit_anomaly": bool(product_id and compatible is False),
            "correction_source": sp.correction_source,
        }

    def update_supplier_product_conditioning(
        self,
        supplier_product_id: int,
        *,
        supplier_unit: str,
        packaging_quantity: Decimal,
        reference_unit: str,
        reference_quantity: Decimal,
        price: Decimal | None = None,
        tax_basis: str | None = None,
        vat_rate: Decimal | None = None,
        update_tax: bool = False,
        clear_vat_rate: bool = False,
    ) -> dict:
        """Correction manuelle du conditionnement (+ prix/TVA optionnels) — prioritaire sur un futur CSV."""
        sp = self.session.get(SupplierProduct, supplier_product_id)
        if sp is None:
            raise LookupError("Référence fournisseur introuvable.")
        if packaging_quantity <= 0 or reference_quantity <= 0:
            raise ValueError("packaging_quantity et reference_quantity doivent être > 0.")
        unit = (supplier_unit or "").strip()
        ref_unit = normalize_unit(reference_unit)
        if not unit:
            raise ValueError("supplier_unit obligatoire.")
        if not ref_unit:
            raise ValueError("reference_unit obligatoire.")
        if tax_basis is not None and tax_basis not in ("HT", "TTC"):
            raise ValueError("tax_basis invalide (HT ou TTC).")
        if vat_rate is not None and not (Decimal("0") <= vat_rate <= Decimal("100")):
            raise ValueError("vat_rate hors plage raisonnable (0–100).")
        sp.supplier_unit = unit[:40]
        sp.packaging_quantity = packaging_quantity
        sp.reference_unit = ref_unit
        sp.reference_quantity = reference_quantity
        sp.correction_source = "manual"

        if update_tax:
            offers = list(
                self.session.scalars(
                    select(Offer).where(Offer.supplier_product_id == sp.id)
                )
            )
            for offer in offers:
                if price is not None:
                    offer.price = price
                if tax_basis is not None:
                    offer.tax_basis = tax_basis
                if vat_rate is not None or clear_vat_rate:
                    offer.vat_rate = vat_rate

        self.session.commit()
        self.session.refresh(sp)
        product = self.session.get(Product, sp.product_id) if sp.product_id else None
        compatible = (
            units_compatible(product.reference_unit, sp.reference_unit) if product else None
        )
        payload = self._conditioning_payload(sp, product, compatible)
        offer = self._latest_offers_for_sps([sp.id]).get(sp.id)
        if offer:
            payload["price"] = str(offer.price) if offer.price is not None else None
            payload["tax_basis"] = offer.tax_basis
            payload["vat_rate"] = str(offer.vat_rate) if offer.vat_rate is not None else None
        return payload

    def clear_conditioning_override(self, supplier_product_id: int) -> dict:
        """Retire l'override manuel : le prochain import CSV pourra réécrire le conditionnement."""
        sp = self.session.get(SupplierProduct, supplier_product_id)
        if sp is None:
            raise LookupError("Référence fournisseur introuvable.")
        sp.correction_source = None
        self.session.commit()
        self.session.refresh(sp)
        product = self.session.get(Product, sp.product_id) if sp.product_id else None
        compatible = (
            units_compatible(product.reference_unit, sp.reference_unit) if product else None
        )
        return self._conditioning_payload(sp, product, compatible)

    def _conditioning_payload(
        self, sp: SupplierProduct, product: Product | None, compatible: bool | None
    ) -> dict:
        return {
            "supplier_product_id": sp.id,
            "supplier_unit": sp.supplier_unit,
            "packaging_quantity": str(sp.packaging_quantity),
            "reference_unit": sp.reference_unit,
            "reference_quantity": str(sp.reference_quantity),
            "product_id": sp.product_id,
            "product_code": product.code if product else None,
            "product_name": product.name if product else None,
            "product_reference_unit": product.reference_unit if product else None,
            "product_attributes": product.attributes if product else None,
            "unit_compatible": compatible,
            "unit_anomaly": bool(product and compatible is False),
            "correction_source": sp.correction_source,
            "updated_at": sp.updated_at.isoformat() if sp.updated_at else None,
        }

    def _product_units(self, product_ids: set[int]) -> dict[int, str]:
        if not product_ids:
            return {}
        return {
            p.id: p.reference_unit
            for p in self.session.scalars(select(Product).where(Product.id.in_(product_ids)))
        }

    def _product_attributes(self, product_ids: set[int]) -> dict[int, dict | None]:
        if not product_ids:
            return {}
        return {
            p.id: p.attributes
            for p in self.session.scalars(select(Product).where(Product.id.in_(product_ids)))
        }

    def _latest_offers_for_sps(self, sp_ids: list[int]) -> dict[int, Offer]:
        if not sp_ids:
            return {}
        rows = self.session.scalars(
            select(Offer).where(Offer.supplier_product_id.in_(sp_ids)).order_by(Offer.id)
        )
        latest: dict[int, Offer] = {}
        for offer in rows:
            latest[offer.supplier_product_id] = offer
        return latest


    def set_catalog_active(self, catalog_id: int, active: bool) -> dict:
        catalog = self.session.get(SupplierImport, catalog_id)
        if catalog is None:
            raise LookupError("Catalogue introuvable.")
        catalog.active = active
        catalog.status = "imported" if active else "deactivated"
        self.session.commit()
        return {"id": catalog.id, "source_key": catalog.source_key, "active": catalog.active}

    def delete_catalog(self, catalog_id: int, *, confirm: bool = False) -> dict:
        if not confirm:
            raise ValueError("Confirmation explicite requise (confirm=true).")
        catalog = self.session.get(SupplierImport, catalog_id)
        if catalog is None:
            raise LookupError("Catalogue introuvable.")
        live_offers = int(
            self.session.scalar(
                select(func.count()).select_from(Offer).where(Offer.catalog_id == catalog.id)
            )
            or 0
        )
        can_delete, reason = self._deletion_policy(catalog, live_offers)
        if not can_delete:
            raise PermissionError(reason or "Suppression refusée.")

        offers = list(
            self.session.scalars(select(Offer).where(Offer.catalog_id == catalog.id))
        )
        deleted_offers = len(offers)
        for offer in offers:
            self.session.delete(offer)
        self.session.flush()

        orphan_sps = list(
            self.session.scalars(
                select(SupplierProduct).where(
                    SupplierProduct.introduced_by_catalog_id == catalog.id,
                    SupplierProduct.product_id.is_(None),
                )
            )
        )
        deleted_refs = 0
        for sp in orphan_sps:
            remaining = self.session.scalar(
                select(func.count())
                .select_from(Offer)
                .where(Offer.supplier_product_id == sp.id)
            )
            if int(remaining or 0) == 0:
                self.session.delete(sp)
                deleted_refs += 1
        self.session.flush()

        remaining_links = list(
            self.session.scalars(
                select(SupplierProduct).where(
                    SupplierProduct.introduced_by_catalog_id == catalog.id
                )
            )
        )
        for sp in remaining_links:
            sp.introduced_by_catalog_id = None
        self.session.flush()

        source_key = catalog.source_key
        self.session.delete(catalog)
        self.session.commit()
        return {
            "deleted": True,
            "source_key": source_key,
            "deleted_offers": deleted_offers,
            "deleted_unmapped_references": deleted_refs,
        }

    def _deletion_policy(
        self, catalog: SupplierImport, live_offers: int
    ) -> tuple[bool, str | None]:
        if catalog.source_key == "demo" or (catalog.filename or "").startswith("demo"):
            return False, "Le catalogue de démonstration ne peut pas être supprimé."
        return True, None

    def _catalog_tax_basis(self, offers: list[NormalizedOffer]) -> str:
        bases = {o.tax_basis for o in offers}
        if len(bases) == 1:
            return next(iter(bases))
        if not bases:
            return "HT"
        return "MIXED"

    def _next_source_key(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        base = f"import-{stamp}"
        key = base
        suffix = 1
        while self.session.scalar(select(SupplierImport).where(SupplierImport.source_key == key)):
            suffix += 1
            key = f"{base}-{suffix:03d}"
        return key

    def _resolve_mappings(
        self, offers: list[NormalizedOffer]
    ) -> dict[tuple[str, str], int | None]:
        """Résout product_id sans suggestion automatique de variantes.

        Ordre : product_code CSV explicite → mapping déjà persisté sur SupplierProduct.
        Jamais de matching flou sur le libellé.
        """
        result: dict[tuple[str, str], int | None] = {}
        for offer in offers:
            key = (offer.supplier, offer.product.external_reference)
            if key in result:
                continue
            product_id: int | None = None
            code = offer.product.product_code
            if code:
                product = self.session.scalar(select(Product).where(Product.code == code))
                if product is not None:
                    product_id = product.id
            if product_id is None:
                supplier = self.session.scalar(
                    select(Supplier).where(Supplier.name == offer.supplier)
                )
                if supplier is not None:
                    existing = self.session.scalar(
                        select(SupplierProduct).where(
                            SupplierProduct.supplier_id == supplier.id,
                            SupplierProduct.supplier_reference
                            == offer.product.external_reference,
                        )
                    )
                    if existing is not None and existing.product_id is not None:
                        product_id = existing.product_id
            result[key] = product_id
        return result

    def _apply_explicit_mappings(
        self,
        resolved: dict[tuple[str, str], int | None],
        mappings: list[dict] | None,
        offers: list[NormalizedOffer],
    ) -> set[tuple[str, str]]:
        """Applique les associations choisies dans l'UI (product_id peut être null)."""
        explicit: set[tuple[str, str]] = set()
        if not mappings:
            return explicit
        offer_keys = {(o.supplier, o.product.external_reference) for o in offers}
        valid_ids = {
            p.id
            for p in self.session.scalars(
                select(Product).where(
                    Product.id.in_(
                        [
                            int(m["product_id"])
                            for m in mappings
                            if m.get("product_id") is not None
                        ]
                        or [-1]
                    )
                )
            )
        }
        for item in mappings:
            supplier = (item.get("supplier") or "").strip()
            ref = (
                item.get("external_reference")
                or item.get("supplier_reference")
                or ""
            ).strip()
            if not supplier or not ref:
                continue
            key = (supplier, ref)
            if key not in offer_keys and key not in resolved:
                continue
            pid = item.get("product_id")
            if pid is not None:
                pid = int(pid)
                if pid not in valid_ids:
                    raise ValueError(f"Produit ProMatConnect introuvable : {pid}")
            resolved[key] = pid
            explicit.add(key)
        return explicit

    def _build_mapping_rows(
        self,
        offers: list[NormalizedOffer],
        mapping: dict[tuple[str, str], int | None],
        product_names: dict[int, str],
    ) -> list[dict]:
        rows: list[dict] = []
        seen: set[tuple[str, str]] = set()
        product_codes = self._product_codes(
            {pid for pid in mapping.values() if pid is not None}
        )
        for offer in offers:
            key = (offer.supplier, offer.product.external_reference)
            if key in seen:
                continue
            seen.add(key)
            pid = mapping.get(key)
            pack = offer.product.packaging_quantity
            ref_qty = offer.product.reference_quantity
            rows.append(
                {
                    "supplier": offer.supplier,
                    "external_reference": offer.product.external_reference,
                    "name": offer.product.name,
                    "brand": offer.product.brand,
                    "supplier_unit": offer.product.supplier_unit,
                    "packaging_quantity": str(pack) if pack is not None else None,
                    "reference_unit": offer.product.reference_unit,
                    "reference_quantity": str(ref_qty) if ref_qty is not None else None,
                    "price": str(offer.price) if offer.price is not None else None,
                    "tax_basis": offer.tax_basis,
                    "vat_rate": str(offer.vat_rate) if offer.vat_rate is not None else None,
                    "image_url": offer.product.image_url,
                    "product_id": pid,
                    "product_code": product_codes.get(pid) if pid else None,
                    "product_name": product_names.get(pid) if pid else None,
                    "mapped": pid is not None,
                }
            )
        return rows

    def _product_names(self, product_ids: set[int]) -> dict[int, str]:
        if not product_ids:
            return {}
        return {
            p.id: p.name
            for p in self.session.scalars(select(Product).where(Product.id.in_(product_ids)))
        }

    def _product_codes(self, product_ids: set[int]) -> dict[int, str]:
        if not product_ids:
            return {}
        return {
            p.id: p.code
            for p in self.session.scalars(select(Product).where(Product.id.in_(product_ids)))
        }

    def _catalog_supplier_products(self, catalog_id: int) -> list[SupplierProduct]:
        via_offers = select(Offer.supplier_product_id).where(Offer.catalog_id == catalog_id)
        return list(
            self.session.scalars(
                select(SupplierProduct)
                .where(
                    or_(
                        SupplierProduct.introduced_by_catalog_id == catalog_id,
                        SupplierProduct.id.in_(via_offers),
                    )
                )
                .order_by(SupplierProduct.supplier_id, SupplierProduct.supplier_reference)
            )
        )

    def _catalog_unmapped_count(self, catalog_id: int) -> int:
        return sum(1 for sp in self._catalog_supplier_products(catalog_id) if sp.product_id is None)

    def _summarize_warnings(
        self, raw: list[dict], offers: list[NormalizedOffer]
    ) -> tuple[list[str], list[dict]]:
        """Infos globales + warnings regroupés (plus de spam national_catalog)."""
        infos: list[str] = []
        if offers and all(not (o.agency.external_id or "").strip() for o in offers):
            infos.append(
                "Catalogue sans magasin : les prix ne sont pas associés à un point de vente "
                "et ne participeront pas aux calculs géographiques."
            )

        counts: dict[str, int] = {}
        others: list[dict] = []
        for w in raw:
            code = w.get("code") if isinstance(w, dict) else None
            if code == "national_catalog":
                continue
            if code in {
                "missing_price",
                "invalid_image_url",
                "invalid_source_url",
                "missing_coords",
                "missing_agency_name",
            }:
                counts[code] = counts.get(code, 0) + 1
            else:
                others.append(w)

        messages: list[dict] = []
        if counts.get("missing_price"):
            n = counts["missing_price"]
            messages.append(
                {
                    "line": 0,
                    "code": "missing_price",
                    "message": (
                        f"{n} référence{'s' if n > 1 else ''} sans prix — "
                        "aucune offre active ne sera créée."
                    ),
                }
            )
        if counts.get("invalid_image_url"):
            n = counts["invalid_image_url"]
            messages.append(
                {
                    "line": 0,
                    "code": "invalid_image_url",
                    "message": f"{n} image_url ignorée(s) (seuls http:// et https:// sont acceptés).",
                }
            )
        if counts.get("invalid_source_url"):
            n = counts["invalid_source_url"]
            messages.append(
                {
                    "line": 0,
                    "code": "invalid_source_url",
                    "message": f"{n} source_url ignorée(s) (seuls http:// et https:// sont acceptés).",
                }
            )
        if counts.get("missing_coords"):
            n = counts["missing_coords"]
            messages.append(
                {
                    "line": 0,
                    "code": "missing_coords",
                    "message": (
                        f"{n} agence(s) sans coordonnées — "
                        "exclue(s) des calculs distance/trajet."
                    ),
                }
            )
        if counts.get("missing_agency_name"):
            n = counts["missing_agency_name"]
            messages.append(
                {
                    "line": 0,
                    "code": "missing_agency_name",
                    "message": f"{n} ligne(s) sans agency_name — nom dérivé du fournisseur.",
                }
            )
        messages.extend(others)
        return infos, messages

    def _upsert_row(
        self,
        offer: NormalizedOffer,
        mapping: dict[tuple[str, str], int | None],
        source_key: str,
        catalog_id: int,
        *,
        explicit_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        explicit_keys = explicit_keys or set()
        supplier = self.session.scalar(select(Supplier).where(Supplier.name == offer.supplier))
        if supplier is None:
            supplier = Supplier(name=offer.supplier, source_type="file", source_key=source_key)
            self.session.add(supplier)
            self.session.flush()
        else:
            supplier.source_type = "file"
            supplier.source_key = source_key

        agency = self.session.scalar(
            select(Agency).where(
                Agency.supplier_id == supplier.id,
                Agency.external_id == (offer.agency.external_id or None),
            )
        )
        has_store = bool((offer.agency.external_id or "").strip())
        if has_store:
            agency_name = offer.agency.name or f"{offer.supplier} · {offer.agency.external_id}"
        else:
            agency_name = offer.agency.name or f"{offer.supplier} (catalogue national)"
        lat = (
            Decimal(str(offer.agency.latitude)) if offer.agency.latitude is not None else None
        )
        lon = (
            Decimal(str(offer.agency.longitude)) if offer.agency.longitude is not None else None
        )
        if (lat is None) != (lon is None):
            lat, lon = None, None
        address = (offer.agency.address or "").strip()
        postal = (offer.agency.postal_code or "").strip()
        city = (offer.agency.city or "").strip()
        if agency is None:
            agency = self.session.scalar(
                select(Agency).where(
                    Agency.supplier_id == supplier.id,
                    Agency.name == agency_name,
                )
            )
        if agency is None:
            agency = Agency(
                supplier_id=supplier.id,
                external_id=(offer.agency.external_id or None) or None,
                name=agency_name[:100],
                address=address[:200] if address else "",
                postal_code=postal[:10] if postal else "",
                city=city[:100] if city else "",
                latitude=lat,
                longitude=lon,
            )
            self.session.add(agency)
            self.session.flush()
        else:
            if offer.agency.external_id:
                agency.external_id = offer.agency.external_id
            agency.name = agency_name[:100]
            if address:
                agency.address = address[:200]
            if postal:
                agency.postal_code = postal[:10]
            if city:
                agency.city = city[:100]
            if offer.agency.latitude is not None and offer.agency.longitude is not None:
                agency.latitude = lat
                agency.longitude = lon

        map_key = (offer.supplier, offer.product.external_reference)
        product_id = mapping.get(map_key)
        pack_qty = offer.product.packaging_quantity or Decimal("1")
        if offer.product.reference_quantity is not None:
            ref_qty = offer.product.reference_quantity
        elif offer.product.packaging_quantity is not None:
            ref_qty = offer.product.packaging_quantity
            pack_qty = Decimal("1")
        else:
            ref_qty = Decimal("1")
        unit = offer.product.supplier_unit or "unité"
        sp = self.session.scalar(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier.id,
                SupplierProduct.supplier_reference == offer.product.external_reference,
            )
        )
        if sp is None:
            sp = SupplierProduct(
                product_id=product_id,
                supplier_id=supplier.id,
                supplier_reference=offer.product.external_reference,
                designation=offer.product.name[:200],
                supplier_unit=unit[:40],
                reference_quantity=ref_qty,
                packaging_quantity=pack_qty,
                reference_unit=offer.product.reference_unit,
                brand=offer.product.brand,
                ean=offer.product.ean,
                image_url=offer.product.image_url,
                active=True,
                introduced_by_catalog_id=catalog_id,
                correction_source="import",
            )
            self.session.add(sp)
            self.session.flush()
        else:
            # Correction manuelle : ne pas écraser mapping / conditionnement.
            if sp.correction_source == "manual":
                if offer.product.brand is not None:
                    sp.brand = offer.product.brand
                if offer.product.ean is not None:
                    sp.ean = offer.product.ean
                if offer.product.image_url is not None:
                    sp.image_url = offer.product.image_url
                sp.designation = offer.product.name[:200]
                sp.active = True
            else:
                if map_key in explicit_keys or product_id is not None:
                    sp.product_id = product_id
                sp.designation = offer.product.name[:200]
                sp.supplier_unit = unit[:40]
                sp.reference_quantity = ref_qty
                sp.packaging_quantity = pack_qty
                if offer.product.reference_unit is not None:
                    sp.reference_unit = offer.product.reference_unit
                if offer.product.brand is not None:
                    sp.brand = offer.product.brand
                if offer.product.ean is not None:
                    sp.ean = offer.product.ean
                if offer.product.image_url is not None:
                    sp.image_url = offer.product.image_url
                sp.active = True
                sp.correction_source = "import"

        # Pour le stock offre : utiliser le conditionnement effectif (éventuellement override).
        effective_ref_qty = sp.reference_quantity
        if offer.price is None:
            return

        if offer.available_quantity is not None and effective_ref_qty > 0:
            stock = int(offer.available_quantity // effective_ref_qty)
        else:
            stock = 0
        prep = offer.preparation_minutes if offer.preparation_minutes is not None else 60
        observed = offer.observed_at or datetime.now(timezone.utc)

        existing_offer = self.session.scalar(
            select(Offer).where(
                Offer.supplier_product_id == sp.id,
                Offer.agency_id == agency.id,
            )
        )
        if existing_offer is None:
            self.session.add(
                Offer(
                    supplier_id=supplier.id,
                    supplier_product_id=sp.id,
                    agency_id=agency.id,
                    catalog_id=catalog_id,
                    price=offer.price,
                    stock=stock,
                    preparation_minutes=prep,
                    currency=offer.currency,
                    tax_basis=offer.tax_basis,
                    vat_rate=offer.vat_rate,
                    source_type="file",
                    source_key=source_key,
                    source_url=offer.source_url,
                    seller=offer.seller,
                    verification_status=offer.verification_status,
                    observed_at=observed,
                )
            )
        else:
            existing_offer.catalog_id = catalog_id
            existing_offer.price = offer.price
            # Override manuel : conserver base / TVA corrigées (pas le prix CSV).
            if sp.correction_source != "manual":
                existing_offer.tax_basis = offer.tax_basis
                existing_offer.vat_rate = offer.vat_rate
            existing_offer.stock = stock
            existing_offer.preparation_minutes = prep
            existing_offer.currency = offer.currency
            existing_offer.source_type = "file"
            existing_offer.source_key = source_key
            existing_offer.source_url = offer.source_url
            existing_offer.seller = offer.seller
            existing_offer.verification_status = offer.verification_status
            existing_offer.observed_at = observed
