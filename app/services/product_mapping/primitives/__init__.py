"""Primitives d'extraction réutilisables — aucune dépendance métier catégorie."""

from app.services.product_mapping.primitives.diameter import (
    extract_diameter_length_mm,
)
from app.services.product_mapping.primitives.dimensions import (
    extract_dimensions_mm,
    extract_length_width_mm,
    extract_thickness_alone_mm,
)
from app.services.product_mapping.primitives.length import extract_bar_length_mm
from app.services.product_mapping.primitives.packaging import (
    extract_lot_quantity,
    extract_piece_count,
)
from app.services.product_mapping.primitives.profile import (
    extract_metal_profile,
    normalize_profile,
)
from app.services.product_mapping.primitives.textutil import (
    fold,
    parse_number,
    round_half_up,
    to_mm,
)

__all__ = [
    "extract_bar_length_mm",
    "extract_diameter_length_mm",
    "extract_dimensions_mm",
    "extract_length_width_mm",
    "extract_lot_quantity",
    "extract_metal_profile",
    "extract_piece_count",
    "extract_thickness_alone_mm",
    "fold",
    "normalize_profile",
    "parse_number",
    "round_half_up",
    "to_mm",
]
