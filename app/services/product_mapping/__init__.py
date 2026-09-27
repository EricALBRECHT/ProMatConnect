"""Package product_mapping — V1 PLAQUE_PLATRE."""

from app.services.product_mapping.service import (
    MappingRunResult,
    ProductMappingService,
    ensure_plaque_platre_category,
)

__all__ = [
    "MappingRunResult",
    "ProductMappingService",
    "ensure_plaque_platre_category",
]
