"""Package product_mapping — PLAQUE_PLATRE + OSSATURE_PLACO."""

from app.services.product_mapping.service import (
    ApplyExactResult,
    ExactApplication,
    MappingRunResult,
    OssatureRunResult,
    ProductMappingService,
    ensure_ossature_placo_category,
    ensure_plaque_platre_category,
    is_exact_applicable,
)

__all__ = [
    "ApplyExactResult",
    "ExactApplication",
    "MappingRunResult",
    "OssatureRunResult",
    "ProductMappingService",
    "ensure_ossature_placo_category",
    "ensure_plaque_platre_category",
    "is_exact_applicable",
]
