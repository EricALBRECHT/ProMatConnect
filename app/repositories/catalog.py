from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Product


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self, query: str = "", limit: int = 100, offset: int = 0) -> list[Product]:
        statement = select(Product)
        if query:
            statement = statement.where(
                or_(
                    Product.name.icontains(query, autoescape=True),
                    Product.code.icontains(query, autoescape=True),
                    Product.category.icontains(query, autoescape=True),
                )
            )
        return list(
            self.session.scalars(statement.order_by(Product.code).limit(limit).offset(offset))
        )

    def get(self, product_id: int) -> Product | None:
        return self.session.get(Product, product_id)

    def get_many(self, product_ids: list[int]) -> list[Product]:
        return list(self.session.scalars(select(Product).where(Product.id.in_(product_ids))))
