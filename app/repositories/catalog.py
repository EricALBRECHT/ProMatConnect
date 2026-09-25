from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Product
from app.models.chantier import ChantierMaterial
from app.schemas.catalog import ProductCreate
from app.services.units import assert_allowed_product_unit


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(
        self,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
        *,
        for_mapping: bool = False,
        include_legacy: bool = False,
        include_inactive: bool = False,
    ) -> list[Product]:
        statement = select(Product)
        if not include_inactive:
            statement = statement.where(Product.is_active.is_(True))
        if for_mapping and not include_legacy:
            statement = statement.where(Product.is_legacy.is_(False))
        if query:
            statement = statement.where(
                or_(
                    Product.name.icontains(query, autoescape=True),
                    Product.code.icontains(query, autoescape=True),
                    Product.category.icontains(query, autoescape=True),
                    Product.subcategory.icontains(query, autoescape=True),
                )
            )
        return list(
            self.session.scalars(statement.order_by(Product.code).limit(limit).offset(offset))
        )

    def get(self, product_id: int) -> Product | None:
        return self.session.get(Product, product_id)

    def get_many(self, product_ids: list[int]) -> list[Product]:
        return list(self.session.scalars(select(Product).where(Product.id.in_(product_ids))))

    def chantier_usage_count(self, product_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(ChantierMaterial)
                .where(ChantierMaterial.product_id == product_id)
            )
            or 0
        )

    def create(self, payload: ProductCreate) -> Product:
        existing = self.session.scalar(select(Product).where(Product.code == payload.code))
        if existing is not None:
            raise ValueError(f"Code produit déjà utilisé : {payload.code}")
        unit = assert_allowed_product_unit(payload.reference_unit)
        product = Product(
            code=payload.code.strip().upper(),
            name=payload.name.strip(),
            category=payload.category.strip(),
            subcategory=(payload.subcategory or "").strip() or None,
            reference_unit=unit,
            description=(payload.description or "").strip() or None,
            attributes=payload.attributes,
            is_active=True,
            is_legacy=False,
        )
        self.session.add(product)
        self.session.commit()
        self.session.refresh(product)
        return product

    def update_reference_unit(self, product_id: int, reference_unit: str) -> Product:
        """Modifie l'unité de besoin si le Product n'est pas utilisé en chantier."""
        product = self.session.get(Product, product_id)
        if product is None:
            raise LookupError("Produit introuvable.")
        unit = assert_allowed_product_unit(reference_unit)
        usage = self.chantier_usage_count(product_id)
        if usage > 0:
            raise PermissionError(
                f"Ce produit est utilisé dans {usage} chantier(s). "
                "Modifier son unité pourrait changer la signification des quantités "
                "historiques. Créez un nouveau Product correctement configuré "
                "plutôt que de modifier celui-ci."
            )
        product.reference_unit = unit
        self.session.commit()
        self.session.refresh(product)
        return product
