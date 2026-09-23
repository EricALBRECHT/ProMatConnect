from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.connectors.registry import build_connectors
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CompareRequest, ComparisonResponse
from app.services.comparison import ComparisonService

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/products", response_model=list[ProductRead], tags=["Catalogue"])
def products(
    session: SessionDependency,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return CatalogRepository(session).list(q.strip(), limit, offset)


@router.get("/products/{product_id}", response_model=ProductRead, tags=["Catalogue"])
def product(product_id: int, session: SessionDependency):
    result = CatalogRepository(session).get(product_id)
    if result is None:
        raise HTTPException(404, "Produit introuvable.")
    return result


@router.post("/compare", response_model=ComparisonResponse, tags=["Comparaison"])
def compare(payload: CompareRequest, request: Request, session: SessionDependency):
    product_ids = [line.product_id for line in payload.lines]
    products = {
        p.id: ProductRead.model_validate(p)
        for p in CatalogRepository(session).get_many(product_ids)
    }
    missing = sorted(set(product_ids) - products.keys())
    if missing:
        raise HTTPException(404, {"message": "Produits introuvables.", "product_ids": missing})
    settings = request.app.state.settings
    return ComparisonService(
        build_connectors(session),
        settings.user_latitude,
        settings.user_longitude,
    ).compare(payload.lines, products)


@router.get("/health", tags=["Exploitation"])
def health(session: SessionDependency):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "version": "0.1.0", "mode": "simulation"}
