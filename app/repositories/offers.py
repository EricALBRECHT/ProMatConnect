from datetime import timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.connectors.base import AgencyData, ConnectorOffer
from app.models import Agency, Offer, Product, Supplier, SupplierImport, SupplierProduct
from app.services.units import units_compatible


class OfferRepository:
    def __init__(self, session: Session):
        self.session = session

    def for_supplier(self, supplier_name: str, product_ids: list[int]) -> list[ConnectorOffer]:
        """Offres actives mappées, catalogue actif (ou démo sans catalogue).

        Les agences sans coords (catalogue national) sont incluses pour le prix
        matériaux ; elles n'inventent pas de latitude/longitude.
        Les offres dont reference_unit est incompatible avec Product sont exclues
        (aucune conversion implicite).
        """
        statement = (
            select(Offer, SupplierProduct, Agency, Product)
            .join(SupplierProduct, Offer.supplier_product_id == SupplierProduct.id)
            .join(Agency, Offer.agency_id == Agency.id)
            .join(Supplier, Offer.supplier_id == Supplier.id)
            .join(Product, SupplierProduct.product_id == Product.id)
            .outerjoin(SupplierImport, Offer.catalog_id == SupplierImport.id)
            .where(
                Supplier.name == supplier_name,
                SupplierProduct.active.is_(True),
                SupplierProduct.product_id.is_not(None),
                SupplierProduct.product_id.in_(product_ids),
                or_(Offer.catalog_id.is_(None), SupplierImport.active.is_(True)),
            )
            .order_by(SupplierProduct.id, Agency.id)
        )
        rows = []
        for offer, product_sp, agency, product in self.session.execute(statement):
            if not units_compatible(product.reference_unit, product_sp.reference_unit):
                continue
            pack_qty = product_sp.packaging_quantity
            if pack_qty is None:
                pack_qty = Decimal("1")
            lat = float(agency.latitude) if agency.latitude is not None else None
            lon = float(agency.longitude) if agency.longitude is not None else None
            rows.append(
                ConnectorOffer(
                    supplier=supplier_name,
                    agency=AgencyData(
                        id=agency.id,
                        name=agency.name,
                        address=agency.address,
                        postal_code=agency.postal_code,
                        city=agency.city,
                        latitude=lat,
                        longitude=lon,
                    ),
                    product_id=int(product_sp.product_id),
                    supplier_reference=product_sp.supplier_reference,
                    supplier_unit=product_sp.supplier_unit,
                    reference_quantity=product_sp.reference_quantity,
                    reference_unit=product_sp.reference_unit or product.reference_unit,
                    packaging_quantity=pack_qty,
                    price=offer.price,
                    tax_basis=offer.tax_basis or "HT",
                    vat_rate=offer.vat_rate,
                    currency=offer.currency or "EUR",
                    stock=offer.stock,
                    preparation_minutes=offer.preparation_minutes,
                    updated_at=offer.updated_at.replace(tzinfo=timezone.utc)
                    if offer.updated_at.tzinfo is None
                    else offer.updated_at,
                    image_url=product_sp.image_url,
                )
            )
        return rows
