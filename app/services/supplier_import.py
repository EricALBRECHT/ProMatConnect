"""Preview + import CSV fournisseur — analyse puis écriture transactionnelle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.file_csv import FileSupplierConnector
from app.connectors.normalized import NormalizedOffer
from app.models import Agency, Offer, Product, Supplier, SupplierImport, SupplierProduct

# Plus de fallback Paris : une agence sans coords reste non géolocalisée.


@dataclass
class UnmappedProductInfo:
    external_reference: str
    name: str
    supplier: str


@dataclass
class ImportPreviewReport:
    valid: bool
    rows: int = 0
    agencies: int = 0
    supplier_references: int = 0
    offers: int = 0
    mapped: int = 0
    unmapped: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    unmapped_products: list[UnmappedProductInfo] = field(default_factory=list)
    suppliers: list[str] = field(default_factory=list)
    filename: str = ""
    source_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "rows": self.rows,
            "agencies": self.agencies,
            "supplier_references": self.supplier_references,
            "offers": self.offers,
            "mapped": self.mapped,
            "unmapped": self.unmapped,
            "errors": self.errors,
            "warnings": self.warnings,
            "unmapped_products": [
                {
                    "external_reference": u.external_reference,
                    "name": u.name,
                    "supplier": u.supplier,
                    "status": "Non associé",
                }
                for u in self.unmapped_products
            ],
            "suppliers": self.suppliers,
            "filename": self.filename,
            "source_key": self.source_key,
        }


@dataclass
class ImportCommitResult:
    valid: bool
    source_key: str
    rows: int
    agencies: int
    supplier_references: int
    offers: int
    mapped: int
    unmapped: int
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "source_key": self.source_key,
            "rows": self.rows,
            "agencies": self.agencies,
            "supplier_references": self.supplier_references,
            "offers": self.offers,
            "mapped": self.mapped,
            "unmapped": self.unmapped,
            "errors": self.errors,
        }


class SupplierImportService:
    def __init__(self, session: Session):
        self.session = session

    def preview(self, data: bytes, filename: str | None) -> ImportPreviewReport:
        """Validation + matching — aucune écriture en base."""
        try:
            safe_name = FileSupplierConnector.validate_filename(filename)
        except ValueError as error:
            return ImportPreviewReport(
                valid=False,
                errors=[{"line": 0, "code": "filename", "message": str(error)}],
                filename=filename or "",
            )

        connector, errors, warnings = FileSupplierConnector.parse_upload(data, filename=safe_name)
        offers = connector.normalized_offers
        if errors:
            return ImportPreviewReport(
                valid=False,
                rows=len(offers),
                errors=errors,
                warnings=warnings,
                filename=safe_name,
            )

        mapping = self._resolve_mappings(offers)
        mapped_refs = {k for k, v in mapping.items() if v is not None}
        unmapped_infos = []
        seen_unmapped: set[tuple[str, str]] = set()
        for offer in offers:
            key = (offer.supplier, offer.product.external_reference)
            if key in mapped_refs:
                continue
            if key in seen_unmapped:
                continue
            seen_unmapped.add(key)
            unmapped_infos.append(
                UnmappedProductInfo(
                    external_reference=offer.product.external_reference,
                    name=offer.product.name,
                    supplier=offer.supplier,
                )
            )

        agencies = {(o.supplier, o.agency.external_id) for o in offers}
        refs = {(o.supplier, o.product.external_reference) for o in offers}
        suppliers = sorted({o.supplier for o in offers})
        mapped_count = len({k for k in refs if k in mapped_refs})
        unmapped_count = len(refs) - mapped_count

        return ImportPreviewReport(
            valid=True,
            rows=len(offers),
            agencies=len(agencies),
            supplier_references=len(refs),
            offers=len(offers),
            mapped=mapped_count,
            unmapped=unmapped_count,
            errors=[],
            warnings=warnings,
            unmapped_products=unmapped_infos,
            suppliers=suppliers,
            filename=safe_name,
        )

    def import_file(self, data: bytes, filename: str | None) -> ImportCommitResult:
        """Ré-analyse ; si valide, upsert transactionnel Agency / SupplierProduct / Offer."""
        report = self.preview(data, filename)
        if not report.valid:
            return ImportCommitResult(
                valid=False,
                source_key="",
                rows=report.rows,
                agencies=0,
                supplier_references=0,
                offers=0,
                mapped=0,
                unmapped=0,
                errors=report.errors,
            )

        connector, errors, _warnings = FileSupplierConnector.parse_upload(
            data, filename=report.filename
        )
        if errors:
            return ImportCommitResult(
                valid=False,
                source_key="",
                rows=0,
                agencies=0,
                supplier_references=0,
                offers=0,
                mapped=0,
                unmapped=0,
                errors=errors,
            )

        offers = connector.normalized_offers
        source_key = self._next_source_key()
        mapping = self._resolve_mappings(offers)

        try:
            for offer in offers:
                self._upsert_offer(offer, mapping, source_key)
            self.session.add(
                SupplierImport(
                    source_key=source_key,
                    filename=report.filename,
                    supplier_name=", ".join(report.suppliers)[:100],
                    status="imported",
                    rows=report.rows,
                    agencies=report.agencies,
                    supplier_references=report.supplier_references,
                    offers=report.offers,
                    mapped=report.mapped,
                    unmapped=report.unmapped,
                    error_count=0,
                )
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return ImportCommitResult(
            valid=True,
            source_key=source_key,
            rows=report.rows,
            agencies=report.agencies,
            supplier_references=report.supplier_references,
            offers=report.offers,
            mapped=report.mapped,
            unmapped=report.unmapped,
        )

    def list_imports(self, limit: int = 20) -> list[SupplierImport]:
        return list(
            self.session.scalars(
                select(SupplierImport).order_by(SupplierImport.created_at.desc()).limit(limit)
            )
        )

    def list_sources(self) -> list[dict]:
        """Sources actives (fournisseurs + dernière observation)."""
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
        """(supplier, external_ref) → product_id | None."""
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

    def _upsert_offer(
        self,
        offer: NormalizedOffer,
        mapping: dict[tuple[str, str], int | None],
        source_key: str,
    ) -> None:
        supplier = self.session.scalar(select(Supplier).where(Supplier.name == offer.supplier))
        if supplier is None:
            supplier = Supplier(
                name=offer.supplier,
                source_type="file",
                source_key=source_key,
            )
            self.session.add(supplier)
            self.session.flush()
        else:
            supplier.source_type = "file"
            supplier.source_key = source_key

        agency = self.session.scalar(
            select(Agency).where(
                Agency.supplier_id == supplier.id,
                Agency.external_id == offer.agency.external_id,
            )
        )
        agency_name = offer.agency.name or f"{offer.supplier} · {offer.agency.external_id}"
        lat = (
            Decimal(str(offer.agency.latitude)) if offer.agency.latitude is not None else None
        )
        lon = (
            Decimal(str(offer.agency.longitude)) if offer.agency.longitude is not None else None
        )
        # Une seule coordonnée fournie → traiter comme non géolocalisée (pas de demi-défaut).
        if (lat is None) != (lon is None):
            lat, lon = None, None
        if agency is None:
            # Repli : même nom historique sans external_id
            agency = self.session.scalar(
                select(Agency).where(
                    Agency.supplier_id == supplier.id,
                    Agency.name == agency_name,
                )
            )
        if agency is None:
            agency = Agency(
                supplier_id=supplier.id,
                external_id=offer.agency.external_id,
                name=agency_name,
                address=offer.agency.address or "Adresse non renseignée",
                postal_code=offer.agency.postal_code or "00000",
                city=offer.agency.city or "Ville non renseignée",
                latitude=lat,
                longitude=lon,
            )
            self.session.add(agency)
            self.session.flush()
        else:
            agency.external_id = offer.agency.external_id
            agency.name = agency_name
            if offer.agency.address:
                agency.address = offer.agency.address
            if offer.agency.postal_code:
                agency.postal_code = offer.agency.postal_code
            if offer.agency.city:
                agency.city = offer.agency.city
            # Ne jamais inventer de coords ; n'écraser que si le CSV en fournit une paire.
            if offer.agency.latitude is not None and offer.agency.longitude is not None:
                agency.latitude = lat
                agency.longitude = lon

        product_id = mapping.get((offer.supplier, offer.product.external_reference))
        pack = offer.product.packaging_quantity or Decimal("1")
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
                reference_quantity=pack,
                brand=offer.product.brand,
                ean=offer.product.ean,
                active=True,
            )
            self.session.add(sp)
            self.session.flush()
        else:
            # Ne pas écraser un mapping existant avec null ; accepter un nouveau mapping explicite.
            if product_id is not None:
                sp.product_id = product_id
            sp.designation = offer.product.name[:200]
            sp.supplier_unit = unit[:40]
            sp.reference_quantity = pack
            if offer.product.brand is not None:
                sp.brand = offer.product.brand
            if offer.product.ean is not None:
                sp.ean = offer.product.ean
            sp.active = True

        if offer.available_quantity is not None and pack > 0:
            stock = int(offer.available_quantity // pack)
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
                    price=offer.price,
                    stock=stock,
                    preparation_minutes=prep,
                    currency=offer.currency,
                    tax_basis=offer.tax_basis,
                    source_type="file",
                    source_key=source_key,
                    observed_at=observed,
                    updated_at=observed,
                )
            )
        else:
            existing_offer.price = offer.price
            existing_offer.stock = stock
            existing_offer.preparation_minutes = prep
            existing_offer.currency = offer.currency
            existing_offer.tax_basis = offer.tax_basis
            existing_offer.source_type = "file"
            existing_offer.source_key = source_key
            existing_offer.observed_at = observed
            existing_offer.updated_at = observed
