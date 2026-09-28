"""API back-office catalogue ProMatConnect."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.schemas.admin_catalogue import (
    CatalogueProductCreate,
    CatalogueProductDetail,
    CatalogueProductListResponse,
    CatalogueProductUpdate,
    CatalogueReferenceSearchResponse,
    CatalogueUnmappedResponse,
)
from app.services.admin_catalogue import AdminCatalogueService

router = APIRouter(prefix="/api/admin/catalogue", tags=["Admin catalogue"])


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/products", response_model=CatalogueProductListResponse)
def admin_catalogue_products(
    session: SessionDependency,
    q: str = Query(default="", max_length=100),
    category: str | None = Query(default=None, max_length=80),
    legacy: str = Query(default="exclude", pattern="^(all|exclude|only)$"),
    active: str = Query(default="active", pattern="^(all|active|inactive)$"),
    mapping: str = Query(default="all", pattern="^(all|mapped|unmapped)$"),
    anomaly: str = Query(default="all", pattern="^(all|with|without)$"),
    has_price: str = Query(default="all", pattern="^(all|with|without)$"),
    supplier: str | None = Query(
        default=None,
        max_length=20,
        description="ID fournisseur (suppliers.id) ou vide pour tous",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    return AdminCatalogueService(session).list_products(
        q=q.strip(),
        category=category,
        legacy=legacy,
        active=active,
        mapping=mapping,
        anomaly=anomaly,
        has_price=has_price,
        supplier=(supplier or "").strip() or None,
        page=page,
        page_size=page_size,
    )


@router.post("/products", response_model=CatalogueProductDetail, status_code=201)
def admin_catalogue_create_product(payload: CatalogueProductCreate, session: SessionDependency):
    try:
        return AdminCatalogueService(session).create_manual_product(payload)
    except ValueError as error:
        message = str(error)
        status = 409 if "déjà utilisé" in message else 400
        raise HTTPException(status, message) from error


@router.get("/references", response_model=CatalogueReferenceSearchResponse)
def admin_catalogue_references(
    session: SessionDependency,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=30),
):
    return AdminCatalogueService(session).search_references(q=q.strip(), limit=limit)


@router.get("/products/{product_id}", response_model=CatalogueProductDetail)
def admin_catalogue_product(product_id: int, session: SessionDependency):
    try:
        return AdminCatalogueService(session).get_product(product_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.patch("/products/{product_id}", response_model=CatalogueProductDetail)
def admin_catalogue_update_product(
    product_id: int, payload: CatalogueProductUpdate, session: SessionDependency
):
    try:
        return AdminCatalogueService(session).update_product(product_id, payload)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/unmapped", response_model=CatalogueUnmappedResponse)
def admin_catalogue_unmapped(
    session: SessionDependency,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    return AdminCatalogueService(session).list_unmapped(
        q=q.strip(), page=page, page_size=page_size
    )
