from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import AgencyData, ConnectorOffer
from app.models import Agency, Offer, Supplier, SupplierProduct


class OfferRepository:
    def __init__(self, session: Session):
        self.session = session

    def for_supplier(self, supplier_name: str, product_ids: list[int]) -> list[ConnectorOffer]:
        statement = (
            select(Offer, SupplierProduct, Agency)
            .join(SupplierProduct, Offer.supplier_product_id == SupplierProduct.id)
            .join(Agency, Offer.agency_id == Agency.id)
            .join(Supplier, Offer.supplier_id == Supplier.id)
            .where(
                Supplier.name == supplier_name,
                SupplierProduct.active.is_(True),
                SupplierProduct.product_id.is_not(None),
                SupplierProduct.product_id.in_(product_ids),
                # Agences non géolocalisées : excluses (pas de fausse distance).
                Agency.latitude.is_not(None),
                Agency.longitude.is_not(None),
            )
            .order_by(SupplierProduct.id, Agency.id)
        )
        return [
            ConnectorOffer(
                supplier=supplier_name,
                agency=AgencyData(
                    id=agency.id,
                    name=agency.name,
                    address=agency.address,
                    postal_code=agency.postal_code,
                    city=agency.city,
                    latitude=float(agency.latitude),
                    longitude=float(agency.longitude),
                ),
                product_id=int(product.product_id),
                supplier_reference=product.supplier_reference,
                supplier_unit=product.supplier_unit,
                reference_quantity=product.reference_quantity,
                price=offer.price,
                stock=offer.stock,
                preparation_minutes=offer.preparation_minutes,
                updated_at=offer.updated_at.replace(tzinfo=timezone.utc)
                if offer.updated_at.tzinfo is None
                else offer.updated_at,
            )
            for offer, product, agency in self.session.execute(statement)
        ]
