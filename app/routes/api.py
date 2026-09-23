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
from app.services.geocoding import AddressNotFound, FakeGeocodingService, GeocodingService
from app.services.optimization import OptimizationLimitError
from app.services.origin import OriginService
from app.services.procurement_cost import CostParameters
from app.services.routing import FakeRoutingService, RoutingService

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


def get_geocoding_service() -> GeocodingService:
    return FakeGeocodingService()


def get_routing_service() -> RoutingService:
    return FakeRoutingService()


@router.post("/compare", response_model=ComparisonResponse, tags=["Comparaison"])
def compare(
    payload: CompareRequest,
    request: Request,
    session: SessionDependency,
    geocoder: Annotated[GeocodingService, Depends(get_geocoding_service)],
    routing: Annotated[RoutingService, Depends(get_routing_service)],
):
    product_ids = [line.product_id for line in payload.lines]
    products = {
        p.id: ProductRead.model_validate(p)
        for p in CatalogRepository(session).get_many(product_ids)
    }
    missing = sorted(set(product_ids) - products.keys())
    if missing:
        raise HTTPException(404, {"message": "Produits introuvables.", "product_ids": missing})
    settings = request.app.state.settings
    try:
        origin = OriginService(settings, geocoder).resolve(payload.origin)
        return ComparisonService(
            build_connectors(session),
            origin.latitude,
            origin.longitude,
            origin=origin,
            routing=routing,
            cost_parameters=CostParameters(
                cost_per_km=settings.cost_per_km,
                time_value_per_hour=settings.time_value_per_hour,
                extra_stop_cost=settings.extra_stop_cost,
            ),
            max_agencies=settings.optimizer_max_agencies,
        ).compare(payload.lines, products)
    except (AddressNotFound, OptimizationLimitError) as error:
        raise HTTPException(422, str(error)) from error


@router.get("/health", tags=["Exploitation"])
def health(session: SessionDependency):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "version": "0.2.0", "mode": "simulation"}
