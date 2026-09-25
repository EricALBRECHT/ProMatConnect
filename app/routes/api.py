from collections.abc import Iterator
from typing import Annotated

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.connectors.file_csv import MAX_UPLOAD_BYTES
from app.connectors.registry import build_connectors
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import ProductCreate, ProductRead
from app.schemas.comparison import CompareRequest, ComparisonResponse
from app.services.comparison import ComparisonService
from app.services.geocoding import AddressNotFound, FakeGeocodingService, GeocodingService
from app.services.optimization import OptimizationLimitError
from app.services.origin import OriginService
from app.services.procurement_cost import CostParameters
from app.services.routing import FakeRoutingService, RoutingService
from app.services.supplier_import import SupplierImportService
from app.services.units import ALLOWED_PRODUCT_UNITS
from app.version import APP_VERSION

router = APIRouter(prefix="/api")


class SupplierProductMappingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int | None = None


class ProductUnitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_unit: str = Field(min_length=1, max_length=30)


class SupplierProductConditioningUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_unit: str = Field(min_length=1, max_length=40)
    packaging_quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    reference_unit: str = Field(min_length=1, max_length=30)
    reference_quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    # Prix source / fiscalité (optionnels) — ne convertissent jamais le price.
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    tax_basis: str | None = Field(default=None, pattern="^(HT|TTC)$")
    vat_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)


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
    for_mapping: bool = Query(
        default=False,
        description="Exclut les Product legacy/démo (recommandé pour l'UI de mapping).",
    ),
    include_legacy: bool = Query(default=False),
):
    return CatalogRepository(session).list(
        q.strip(),
        limit,
        offset,
        for_mapping=for_mapping,
        include_legacy=include_legacy,
    )


@router.post("/products", response_model=ProductRead, tags=["Catalogue"], status_code=201)
def create_product(payload: ProductCreate, session: SessionDependency):
    """Création explicite d'un Product normalisé — jamais depuis un libellé fournisseur."""
    try:
        return CatalogRepository(session).create(payload)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/products/{product_id}", response_model=ProductRead, tags=["Catalogue"])
def product(product_id: int, session: SessionDependency):
    result = CatalogRepository(session).get(product_id)
    if result is None:
        raise HTTPException(404, "Produit introuvable.")
    return result


@router.get("/products/{product_id}/usage", tags=["Catalogue"])
def product_usage(product_id: int, session: SessionDependency):
    repo = CatalogRepository(session)
    product = repo.get(product_id)
    if product is None:
        raise HTTPException(404, "Produit introuvable.")
    count = repo.chantier_usage_count(product_id)
    return {
        "product_id": product_id,
        "code": product.code,
        "reference_unit": product.reference_unit,
        "attributes": product.attributes,
        "chantier_materials_count": count,
        "unit_editable": count == 0,
        "allowed_units": list(ALLOWED_PRODUCT_UNITS),
    }


@router.patch("/products/{product_id}/reference-unit", tags=["Catalogue"])
def patch_product_reference_unit(
    product_id: int, payload: ProductUnitUpdate, session: SessionDependency
):
    """Corrige l'unité de besoin. Bloqué si le Product est utilisé en chantier."""
    repo = CatalogRepository(session)
    try:
        product = repo.get(product_id)
        if product is None:
            raise LookupError("Produit introuvable.")
        usage = repo.chantier_usage_count(product_id)
        if usage > 0:
            raise HTTPException(
                409,
                {
                    "code": "product_in_use",
                    "chantier_count": usage,
                    "message": (
                        f"Ce produit est utilisé dans {usage} chantier(s). "
                        "Modifier son unité pourrait changer la signification des "
                        "quantités historiques. Créez un nouveau Product."
                    ),
                },
            )
        updated = repo.update_reference_unit(product_id, payload.reference_unit)
        return ProductRead.model_validate(updated)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except PermissionError as error:
        raise HTTPException(409, {"code": "product_in_use", "message": str(error)}) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.put(
    "/supplier-products/{supplier_product_id}/conditioning",
    tags=["Imports fournisseurs"],
)
def put_supplier_product_conditioning(
    supplier_product_id: int,
    payload: SupplierProductConditioningUpdate,
    session: SessionDependency,
):
    try:
        tax_fields = {"price", "tax_basis", "vat_rate"} & payload.model_fields_set
        return SupplierImportService(session).update_supplier_product_conditioning(
            supplier_product_id,
            supplier_unit=payload.supplier_unit,
            packaging_quantity=payload.packaging_quantity,
            reference_unit=payload.reference_unit,
            reference_quantity=payload.reference_quantity,
            price=payload.price,
            tax_basis=payload.tax_basis,
            vat_rate=payload.vat_rate,
            update_tax=bool(tax_fields),
            clear_vat_rate="vat_rate" in tax_fields and payload.vat_rate is None,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.delete(
    "/supplier-products/{supplier_product_id}/conditioning-override",
    tags=["Imports fournisseurs"],
)
def delete_conditioning_override(supplier_product_id: int, session: SessionDependency):
    """Supprime l'override manuel — le prochain import CSV pourra réécrire le conditionnement."""
    try:
        return SupplierImportService(session).clear_conditioning_override(supplier_product_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


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
            tax_basis=payload.tax_basis,
        ).compare(payload.lines, products)
    except (AddressNotFound, OptimizationLimitError) as error:
        raise HTTPException(422, str(error)) from error


@router.get("/health", tags=["Exploitation"])
def health(session: SessionDependency):
    session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "version": APP_VERSION,
        "mode": "catalog",
        "data_sources": ["demo", "file"],
    }


async def _read_csv_upload(upload: UploadFile) -> tuple[bytes, str]:
    filename = upload.filename or "import.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(400, "Seuls les fichiers .csv sont acceptés.")
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"Fichier trop volumineux (max {MAX_UPLOAD_BYTES // 1024} Ko).")
    return data, filename


@router.post("/supplier-imports/preview", tags=["Imports fournisseurs"])
async def supplier_import_preview(
    session: SessionDependency,
    file: UploadFile = File(...),
):
    """Valide un CSV sans écrire en base."""
    data, filename = await _read_csv_upload(file)
    return SupplierImportService(session).preview(data, filename).to_dict()


@router.post("/supplier-imports", tags=["Imports fournisseurs"])
async def supplier_import_commit(
    session: SessionDependency,
    file: UploadFile = File(...),
    mappings: str | None = Form(default=None),
):
    """Importe un CSV après re-validation (transaction complète).

    `mappings` (optionnel) : JSON
    [{"supplier":"...","external_reference":"...","product_id":1|null}, ...]
    Association uniquement explicite — aucune suggestion automatique de variantes.
    """
    import json

    data, filename = await _read_csv_upload(file)
    mapping_payload: list[dict] | None = None
    if mappings:
        try:
            parsed = json.loads(mappings)
        except json.JSONDecodeError as error:
            raise HTTPException(400, "mappings JSON invalide.") from error
        if not isinstance(parsed, list):
            raise HTTPException(400, "mappings doit être une liste.")
        mapping_payload = parsed
    try:
        result = SupplierImportService(session).import_file(
            data, filename, mappings=mapping_payload
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not result.valid:
        raise HTTPException(400, result.to_dict())
    return result.to_dict()


@router.get("/supplier-imports", tags=["Imports fournisseurs"])
def supplier_import_history(session: SessionDependency, limit: int = Query(20, ge=1, le=100)):
    rows = SupplierImportService(session).list_imports(limit)
    return [
        {
            "id": row.id,
            "source_key": row.source_key,
            "filename": row.filename,
            "supplier_name": row.supplier_name,
            "status": row.status,
            "active": bool(row.active),
            "tax_basis": row.tax_basis,
            "rows": row.rows,
            "rows_with_price": row.rows_with_price,
            "rows_without_price": row.rows_without_price,
            "agencies": row.agencies,
            "offers": row.offers,
            "mapped": row.mapped,
            "unmapped": row.unmapped,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.get("/supplier-catalogs", tags=["Imports fournisseurs"])
def supplier_catalogs(session: SessionDependency):
    return SupplierImportService(session).list_catalogs()


@router.get("/supplier-catalogs/{catalog_id}/mappings", tags=["Imports fournisseurs"])
def catalog_mappings(catalog_id: int, session: SessionDependency):
    try:
        return SupplierImportService(session).list_catalog_mappings(catalog_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.put(
    "/supplier-products/{supplier_product_id}/mapping",
    tags=["Imports fournisseurs"],
)
def set_supplier_product_mapping(
    supplier_product_id: int,
    payload: SupplierProductMappingUpdate,
    session: SessionDependency,
):
    """Définit ou efface (product_id: null) le mapping d'une référence fournisseur."""
    try:
        return SupplierImportService(session).set_supplier_product_mapping(
            supplier_product_id, payload.product_id
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/supplier-catalogs/{catalog_id}/activate", tags=["Imports fournisseurs"])
def activate_catalog(catalog_id: int, session: SessionDependency):
    try:
        return SupplierImportService(session).set_catalog_active(catalog_id, True)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/supplier-catalogs/{catalog_id}/deactivate", tags=["Imports fournisseurs"])
def deactivate_catalog(catalog_id: int, session: SessionDependency):
    try:
        return SupplierImportService(session).set_catalog_active(catalog_id, False)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.delete("/supplier-catalogs/{catalog_id}", tags=["Imports fournisseurs"])
def delete_catalog(
    catalog_id: int,
    session: SessionDependency,
    confirm: bool = Query(False),
):
    try:
        return SupplierImportService(session).delete_catalog(catalog_id, confirm=confirm)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except PermissionError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/supplier-sources", tags=["Imports fournisseurs"])
def supplier_sources(session: SessionDependency):
    return SupplierImportService(session).list_sources()
