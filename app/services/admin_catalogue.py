"""Back-office catalogue ProMatConnect — agrégation Product / SP / Offers / prix HT-TTC."""

from __future__ import annotations

from decimal import Decimal
from math import ceil

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Agency, Offer, Product, Supplier, SupplierProduct
from app.repositories.catalog import CatalogRepository
from app.schemas.admin_catalogue import (
    CatalogueOfferView,
    CatalogueProductDetail,
    CatalogueProductListItem,
    CatalogueProductListResponse,
    CatalogueProductUpdate,
    CatalogueSupplierProductView,
    CatalogueUnmappedItem,
    CatalogueUnmappedResponse,
)
from app.services.tax import display_converted
from app.services.units import ALLOWED_PRODUCT_UNITS, units_compatible


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _min_ignore_none(values: list[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return min(present)


class AdminCatalogueService:
    def __init__(self, session: Session):
        self.session = session
        self.catalog = CatalogRepository(session)

    def list_products(
        self,
        *,
        q: str = "",
        category: str | None = None,
        legacy: str = "exclude",  # all | exclude | only
        active: str = "active",  # all | active | inactive
        mapping: str = "all",  # all | mapped | unmapped
        anomaly: str = "all",  # all | with | without
        has_price: str = "all",  # all | with | without
        page: int = 1,
        page_size: int = 25,
    ) -> CatalogueProductListResponse:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)

        statement = select(Product)
        if active == "active":
            statement = statement.where(Product.is_active.is_(True))
        elif active == "inactive":
            statement = statement.where(Product.is_active.is_(False))
        if legacy == "exclude":
            statement = statement.where(Product.is_legacy.is_(False))
        elif legacy == "only":
            statement = statement.where(Product.is_legacy.is_(True))
        if category:
            statement = statement.where(Product.category == category)
        if q:
            statement = statement.where(
                or_(
                    Product.name.icontains(q, autoescape=True),
                    Product.code.icontains(q, autoescape=True),
                    Product.category.icontains(q, autoescape=True),
                    Product.subcategory.icontains(q, autoescape=True),
                )
            )

        products = list(self.session.scalars(statement.order_by(Product.code)))
        stats = self._stats_by_product_id([p.id for p in products])

        items: list[CatalogueProductListItem] = []
        for product in products:
            st = stats.get(product.id, {})
            item = CatalogueProductListItem(
                id=product.id,
                code=product.code,
                name=product.name,
                category=product.category,
                subcategory=product.subcategory,
                reference_unit=product.reference_unit,
                is_active=bool(product.is_active),
                is_legacy=bool(product.is_legacy),
                image_url=st.get("image_url"),
                attributes=product.attributes,
                supplier_product_count=int(st.get("sp_count") or 0),
                offer_count=int(st.get("offer_count") or 0),
                anomaly_count=int(st.get("anomaly_count") or 0),
                min_price_ht=st.get("min_price_ht"),
                min_price_ttc=st.get("min_price_ttc"),
                has_price=bool(st.get("offer_count")),
            )
            if mapping == "mapped" and item.supplier_product_count == 0:
                continue
            if mapping == "unmapped" and item.supplier_product_count > 0:
                continue
            if anomaly == "with" and item.anomaly_count == 0:
                continue
            if anomaly == "without" and item.anomaly_count > 0:
                continue
            if has_price == "with" and not item.has_price:
                continue
            if has_price == "without" and item.has_price:
                continue
            items.append(item)

        total = len(items)
        pages = max(1, ceil(total / page_size)) if total else 1
        if page > pages:
            page = pages
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]

        categories = sorted(
            {
                c
                for c in self.session.scalars(
                    select(Product.category).where(Product.is_active.is_(True)).distinct()
                )
                if c
            }
        )
        unmapped_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(SupplierProduct)
                .where(SupplierProduct.product_id.is_(None), SupplierProduct.active.is_(True))
            )
            or 0
        )
        anomaly_product_count = sum(1 for i in items if i.anomaly_count > 0)
        legacy_count = int(
            self.session.scalar(
                select(func.count()).select_from(Product).where(Product.is_legacy.is_(True))
            )
            or 0
        )

        return CatalogueProductListResponse(
            items=page_items,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
            categories=categories,
            unmapped_count=unmapped_count,
            anomaly_product_count=anomaly_product_count,
            legacy_count=legacy_count,
        )

    def get_product(self, product_id: int) -> CatalogueProductDetail:
        product = self.catalog.get(product_id)
        if product is None:
            raise LookupError("Produit introuvable.")
        sps = list(
            self.session.scalars(
                select(SupplierProduct)
                .where(SupplierProduct.product_id == product_id)
                .order_by(SupplierProduct.supplier_id, SupplierProduct.supplier_reference)
            )
        )
        suppliers = {
            s.id: s.name
            for s in self.session.scalars(
                select(Supplier).where(Supplier.id.in_({sp.supplier_id for sp in sps} or {-1}))
            )
        }
        offers_by_sp = self._offers_by_sp([sp.id for sp in sps])
        agencies = self._agency_names(
            {o.agency_id for rows in offers_by_sp.values() for o in rows}
        )

        sp_views: list[CatalogueSupplierProductView] = []
        all_ht: list[Decimal | None] = []
        all_ttc: list[Decimal | None] = []
        anomaly_count = 0
        offer_count = 0
        image_url = None

        for sp in sps:
            compatible = units_compatible(product.reference_unit, sp.reference_unit)
            anomaly = bool(compatible is False)
            if anomaly:
                anomaly_count += 1
            offer_rows = offers_by_sp.get(sp.id, [])
            offer_views: list[CatalogueOfferView] = []
            ht_prices: list[Decimal | None] = []
            ttc_prices: list[Decimal | None] = []
            for offer in offer_rows:
                price_ht = display_converted(
                    offer.price, offer.tax_basis or "HT", offer.vat_rate, "HT"
                )
                price_ttc = display_converted(
                    offer.price, offer.tax_basis or "HT", offer.vat_rate, "TTC"
                )
                ht_prices.append(price_ht)
                ttc_prices.append(price_ttc)
                offer_views.append(
                    CatalogueOfferView(
                        offer_id=offer.id,
                        agency_id=offer.agency_id,
                        agency_name=agencies.get(offer.agency_id, f"Agence #{offer.agency_id}"),
                        price=offer.price,
                        tax_basis=offer.tax_basis or "HT",
                        vat_rate=offer.vat_rate,
                        price_ht=price_ht,
                        price_ttc=price_ttc,
                        currency=offer.currency or "EUR",
                        stock=int(offer.stock or 0),
                        source_type=offer.source_type,
                        catalog_id=offer.catalog_id,
                        source_url=offer.source_url,
                        observed_at=offer.observed_at.isoformat() if offer.observed_at else None,
                    )
                )
            offer_count += len(offer_views)
            sp_min_ht = _min_ignore_none(ht_prices)
            sp_min_ttc = _min_ignore_none(ttc_prices)
            all_ht.append(sp_min_ht)
            all_ttc.append(sp_min_ttc)
            if image_url is None and sp.image_url:
                image_url = sp.image_url
            first = offer_rows[0] if offer_rows else None
            sp_views.append(
                CatalogueSupplierProductView(
                    supplier_product_id=sp.id,
                    supplier=suppliers.get(sp.supplier_id, ""),
                    supplier_reference=sp.supplier_reference,
                    designation=sp.designation,
                    brand=sp.brand,
                    ean=sp.ean,
                    image_url=sp.image_url,
                    supplier_unit=sp.supplier_unit,
                    packaging_quantity=sp.packaging_quantity,
                    reference_unit=sp.reference_unit,
                    reference_quantity=sp.reference_quantity,
                    correction_source=sp.correction_source,
                    unit_compatible=compatible,
                    unit_anomaly=anomaly,
                    active=bool(sp.active),
                    offers=offer_views,
                    min_price_ht=sp_min_ht,
                    min_price_ttc=sp_min_ttc,
                    catalog_id=first.catalog_id if first else sp.introduced_by_catalog_id,
                    introduced_by_catalog_id=sp.introduced_by_catalog_id,
                    source_url=first.source_url if first else None,
                    observed_at=first.observed_at.isoformat() if first and first.observed_at else None,
                )
            )

        usage = self.catalog.chantier_usage_count(product_id)
        return CatalogueProductDetail(
            id=product.id,
            code=product.code,
            name=product.name,
            category=product.category,
            subcategory=product.subcategory,
            reference_unit=product.reference_unit,
            description=product.description,
            attributes=product.attributes,
            is_active=bool(product.is_active),
            is_legacy=bool(product.is_legacy),
            image_url=image_url,
            chantier_usage_count=usage,
            can_edit_reference_unit=usage == 0,
            supplier_products=sp_views,
            min_price_ht=_min_ignore_none(all_ht),
            min_price_ttc=_min_ignore_none(all_ttc),
            offer_count=offer_count,
            anomaly_count=anomaly_count,
            allowed_reference_units=list(ALLOWED_PRODUCT_UNITS),
        )

    def update_product(self, product_id: int, payload: CatalogueProductUpdate) -> CatalogueProductDetail:
        product = self.catalog.get(product_id)
        if product is None:
            raise LookupError("Produit introuvable.")
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] is not None:
            product.name = data["name"].strip()
        if "category" in data and data["category"] is not None:
            product.category = data["category"].strip()
        if "subcategory" in data:
            raw = data["subcategory"]
            product.subcategory = (raw or "").strip() or None
        if "description" in data:
            raw = data["description"]
            product.description = (raw or "").strip() or None
        if "attributes" in data:
            product.attributes = data["attributes"]
        if "is_active" in data and data["is_active"] is not None:
            product.is_active = bool(data["is_active"])
        self.session.commit()
        self.session.refresh(product)
        return self.get_product(product_id)

    def list_unmapped(
        self, *, q: str = "", page: int = 1, page_size: int = 25
    ) -> CatalogueUnmappedResponse:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        statement = (
            select(SupplierProduct)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(SupplierProduct.product_id.is_(None))
            .order_by(Supplier.name, SupplierProduct.supplier_reference)
        )
        if q:
            statement = statement.where(
                or_(
                    SupplierProduct.designation.icontains(q, autoescape=True),
                    SupplierProduct.supplier_reference.icontains(q, autoescape=True),
                    SupplierProduct.brand.icontains(q, autoescape=True),
                    SupplierProduct.ean.icontains(q, autoescape=True),
                    Supplier.name.icontains(q, autoescape=True),
                )
            )
        sps = list(self.session.scalars(statement))
        total = len(sps)
        pages = max(1, ceil(total / page_size)) if total else 1
        if page > pages:
            page = pages
        start = (page - 1) * page_size
        slice_sps = sps[start : start + page_size]
        suppliers = {
            s.id: s.name
            for s in self.session.scalars(
                select(Supplier).where(
                    Supplier.id.in_({sp.supplier_id for sp in slice_sps} or {-1})
                )
            )
        }
        offers_by_sp = self._offers_by_sp([sp.id for sp in slice_sps])
        items: list[CatalogueUnmappedItem] = []
        for sp in slice_sps:
            offers = offers_by_sp.get(sp.id, [])
            offer = offers[0] if offers else None
            price_ht = (
                display_converted(offer.price, offer.tax_basis or "HT", offer.vat_rate, "HT")
                if offer
                else None
            )
            price_ttc = (
                display_converted(offer.price, offer.tax_basis or "HT", offer.vat_rate, "TTC")
                if offer
                else None
            )
            items.append(
                CatalogueUnmappedItem(
                    supplier_product_id=sp.id,
                    supplier=suppliers.get(sp.supplier_id, ""),
                    supplier_reference=sp.supplier_reference,
                    designation=sp.designation,
                    brand=sp.brand,
                    ean=sp.ean,
                    image_url=sp.image_url,
                    supplier_unit=sp.supplier_unit,
                    packaging_quantity=sp.packaging_quantity,
                    reference_unit=sp.reference_unit,
                    reference_quantity=sp.reference_quantity,
                    price=offer.price if offer else None,
                    tax_basis=offer.tax_basis if offer else None,
                    vat_rate=offer.vat_rate if offer else None,
                    catalog_id=offer.catalog_id if offer else sp.introduced_by_catalog_id,
                    source_url=offer.source_url if offer else None,
                    observed_at=offer.observed_at.isoformat() if offer and offer.observed_at else None,
                    price_ht=price_ht,
                    price_ttc=price_ttc,
                )
            )
        return CatalogueUnmappedResponse(
            items=items, total=total, page=page, page_size=page_size, pages=pages
        )

    def _offers_by_sp(self, sp_ids: list[int]) -> dict[int, list[Offer]]:
        if not sp_ids:
            return {}
        rows = list(
            self.session.scalars(
                select(Offer)
                .where(Offer.supplier_product_id.in_(sp_ids))
                .order_by(Offer.price)
            )
        )
        by_sp: dict[int, list[Offer]] = {}
        for offer in rows:
            by_sp.setdefault(offer.supplier_product_id, []).append(offer)
        return by_sp

    def _agency_names(self, agency_ids: set[int]) -> dict[int, str]:
        if not agency_ids:
            return {}
        return {
            a.id: a.name
            for a in self.session.scalars(select(Agency).where(Agency.id.in_(agency_ids)))
        }

    def _stats_by_product_id(self, product_ids: list[int]) -> dict[int, dict]:
        if not product_ids:
            return {}
        sps = list(
            self.session.scalars(
                select(SupplierProduct).where(SupplierProduct.product_id.in_(product_ids))
            )
        )
        products = {
            p.id: p
            for p in self.session.scalars(select(Product).where(Product.id.in_(product_ids)))
        }
        offers_by_sp = self._offers_by_sp([sp.id for sp in sps])
        stats: dict[int, dict] = {pid: {"sp_count": 0, "offer_count": 0, "anomaly_count": 0,
                                         "min_price_ht": None, "min_price_ttc": None,
                                         "image_url": None, "_hts": [], "_ttcs": []}
                                  for pid in product_ids}
        for sp in sps:
            pid = sp.product_id
            if pid is None or pid not in stats:
                continue
            st = stats[pid]
            st["sp_count"] += 1
            product = products.get(pid)
            if product and units_compatible(product.reference_unit, sp.reference_unit) is False:
                st["anomaly_count"] += 1
            if st["image_url"] is None and sp.image_url:
                st["image_url"] = sp.image_url
            for offer in offers_by_sp.get(sp.id, []):
                st["offer_count"] += 1
                st["_hts"].append(
                    display_converted(offer.price, offer.tax_basis or "HT", offer.vat_rate, "HT")
                )
                st["_ttcs"].append(
                    display_converted(offer.price, offer.tax_basis or "HT", offer.vat_rate, "TTC")
                )
        for st in stats.values():
            st["min_price_ht"] = _min_ignore_none(st.pop("_hts"))
            st["min_price_ttc"] = _min_ignore_none(st.pop("_ttcs"))
        return stats
