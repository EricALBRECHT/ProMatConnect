"""Package product_mapping — V1 PLAQUE_PLATRE."""

from app.services.product_mapping.service import (
    ApplyExactResult,
    ExactApplication,
    MappingRunResult,
    ProductMappingService,
    ensure_plaque_platre_category,
    is_exact_applicable,
)

__all__ = [
    "ApplyExactResult",
    "ExactApplication",
    "MappingRunResult",
    "ProductMappingService",
    "ensure_plaque_platre_category",
    "is_exact_applicable",
]
