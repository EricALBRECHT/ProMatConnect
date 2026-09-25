"""Référentiel Product normalisé ProMatConnect — seed idempotent, additif."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product

# code, name, category, subcategory, reference_unit, attributes, description
NORMALIZED_PRODUCTS: list[tuple] = [
    (
        "PMC-BA13-STD-2500X1200",
        "Plaque BA13 standard 2500 × 1200 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "standard",
            "surface_m2": 3.0,
        },
        "Plaque BA13 standard — besoin chantier = nombre de pièces (surface technique 3,0 m²).",
    ),
    (
        "PMC-BA13-STD-2500X600",
        "Plaque BA13 standard 2500 × 600 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 600,
            "thickness_mm": 13,
            "type": "standard",
            "surface_m2": 1.5,
        },
        "Plaque BA13 standard format étroit — quantité = pièces (surface technique 1,5 m²).",
    ),
    (
        "PMC-BA13-HYDRO-2500X1200",
        "Plaque BA13 hydrofuge 2500 × 1200 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "hydrofuge",
            "surface_m2": 3.0,
        },
        "Plaque BA13 hydrofuge — quantité = pièces (surface technique 3,0 m²).",
    ),
    (
        "PMC-BA13-MULTI-2500X1200",
        "Plaque BA13 multifonctions 2500 × 1200 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "multifonctions",
            "surface_m2": 3.0,
        },
        "Plaque BA13 multifonctions — quantité = pièces (surface technique 3,0 m²).",
    ),
    (
        "PMC-BA13-LIGHT-2500X1200",
        "Plaque BA13 standard légère / Purelight 2500 × 1200 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "legere",
            "surface_m2": 3.0,
        },
        "Plaque BA13 légère / Purelight — quantité = pièces (surface technique 3,0 m²).",
    ),
    (
        "PMC-RAIL-R48-3000",
        "Rail R48 3 m",
        "Ossature",
        "Rails",
        "pièce",
        {"profile": "R48", "length_mm": 3000},
        "Rail métallique R48 (longueur technique 3 m). Quantité chantier = nombre de pièces.",
    ),
    (
        "PMC-RAIL-R70-3000",
        "Rail R70 3 m",
        "Ossature",
        "Rails",
        "pièce",
        {"profile": "R70", "length_mm": 3000},
        "Rail métallique R70 (longueur technique 3 m) — distinct du R48. Quantité = pièces.",
    ),
    (
        "PMC-MONTANT-M48-2500",
        "Montant M48 ~2,50 m",
        "Ossature",
        "Montants",
        "pièce",
        {"profile": "M48", "length_mm": 2500},
        "Montant M48 ~2,50 m. Quantité chantier = nombre de pièces (pas des mètres).",
    ),
    (
        "PMC-MONTANT-M48-3000",
        "Montant M48 3 m",
        "Ossature",
        "Montants",
        "pièce",
        {"profile": "M48", "length_mm": 3000},
        "Montant M48 3 m — distinct du ~2,50 m. Quantité = pièces.",
    ),
    (
        "PMC-FOURRURE-F45-3000",
        "Fourrure F45 3 m",
        "Ossature",
        "Fourrures",
        "pièce",
        {"profile": "F45", "length_mm": 3000},
        "Fourrure F45 (longueur technique 3 m). Quantité chantier = nombre de pièces.",
    ),
    (
        "PMC-VIS-PLACO-35X25",
        "Vis plaque de plâtre 3,5 × 25 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 25, "type": "placo"},
        "Vis placo 3,5×25 — ne pas assimiler à 3,5×35 ni SPAX 3,9×25.",
    ),
    (
        "PMC-VIS-PLACO-35X35",
        "Vis plaque de plâtre 3,5 × 35 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 35, "type": "placo"},
        "Vis placo 3,5×35 — distincte du 3,5×25.",
    ),
    (
        "PMC-BANDE-JOINT-PAPIER",
        "Bande à joint papier",
        "Plâtrerie",
        "Bandes à joint",
        "m",
        {"type": "papier"},
        "Bande à joint papier — distincte de la bande armée.",
    ),
    (
        "PMC-BANDE-ARMEE",
        "Bande armée",
        "Plâtrerie",
        "Bandes à joint",
        "m",
        {"type": "armee"},
        "Bande armée — distincte de la bande papier.",
    ),
    (
        "PMC-ENDUIT-JOINT-PLACO",
        "Enduit joint plaques de plâtre",
        "Plâtrerie",
        "Enduits",
        "kg",
        {"type": "joint_placo"},
        "Enduit de jointoiement pour plaques — non assimilé aux autres plâtres.",
    ),
    (
        "PMC-CIM-CEM2-325",
        "Ciment CEM II 32,5",
        "Gros œuvre",
        "Ciments",
        "kg",
        {"standard": "CEM II", "strength_class": "32,5"},
        "Ciment CEM II classe 32,5 — distinct du 42,5.",
    ),
    (
        "PMC-CIM-CEM2-425",
        "Ciment CEM II 42,5",
        "Gros œuvre",
        "Ciments",
        "kg",
        {"standard": "CEM II", "strength_class": "42,5"},
        "Ciment CEM II classe 42,5 — distinct du 32,5.",
    ),
    (
        "PMC-LDV-MUR-100-L32-R315-KRAFT",
        "Laine de verre mur 100 mm λ0,032 R=3,15 kraft",
        "Isolation",
        "Laines de verre",
        "m²",
        {
            "thickness_mm": 100,
            "thermal_lambda": 0.032,
            "thermal_resistance": 3.15,
            "finish": "kraft",
            "usage": "mur",
        },
        "LDV mur 100 mm λ0,032 R3,15 kraft — distincte du λ0,040.",
    ),
    (
        "PMC-LDV-MUR-120-L32-R375-KRAFT",
        "Laine de verre mur 120 mm λ0,032 R=3,75 kraft",
        "Isolation",
        "Laines de verre",
        "m²",
        {
            "thickness_mm": 120,
            "thermal_lambda": 0.032,
            "thermal_resistance": 3.75,
            "finish": "kraft",
            "usage": "mur",
        },
        "LDV mur 120 mm λ0,032 R3,75 kraft.",
    ),
    (
        "PMC-LDV-CLOISON-70-L40-R175-NU",
        "Laine de verre cloison 70 mm λ0,040 R=1,75 nu",
        "Isolation",
        "Laines de verre",
        "m²",
        {
            "thickness_mm": 70,
            "thermal_lambda": 0.040,
            "thermal_resistance": 1.75,
            "finish": "nu",
            "usage": "cloison",
        },
        "LDV cloison 70 mm λ0,040 R1,75 nu.",
    ),
    (
        "PMC-LDV-MUR-100-L40-R250",
        "Laine de verre 100 mm λ0,040 R=2,5",
        "Isolation",
        "Laines de verre",
        "m²",
        {
            "thickness_mm": 100,
            "thermal_lambda": 0.040,
            "thermal_resistance": 2.5,
            "usage": "mur",
        },
        "LDV 100 mm λ0,040 R2,5 — distincte du 100 mm λ0,032 R3,15.",
    ),
]

NORMALIZED_PRODUCT_COUNT = len(NORMALIZED_PRODUCTS)


def seed_normalized_catalog(session: Session) -> int:
    """Crée les Product normalisés manquants. Corrige l'unité ossature si besoin.

    Ne touche pas aux Product historiques PMC000x (legacy).
    """
    created = 0
    corrected = 0
    for code, name, category, subcategory, unit, attributes, description in NORMALIZED_PRODUCTS:
        existing = session.scalar(select(Product).where(Product.code == code))
        if existing is None:
            session.add(
                Product(
                    code=code,
                    name=name,
                    category=category,
                    subcategory=subcategory,
                    reference_unit=unit,
                    description=description,
                    attributes=attributes,
                    is_active=True,
                    is_legacy=False,
                )
            )
            created += 1
            continue
        # Correction non destructive des Product normalisés (ossature + plaques).
        if code.startswith(("PMC-RAIL-", "PMC-MONTANT-", "PMC-FOURRURE-", "PMC-BA13-")):
            changed = False
            if existing.reference_unit != unit:
                existing.reference_unit = unit
                changed = True
            if existing.attributes != attributes:
                existing.attributes = attributes
                changed = True
            if existing.description != description:
                existing.description = description
                changed = True
            if existing.name != name:
                existing.name = name
                changed = True
            if changed:
                corrected += 1
    if created or corrected:
        session.flush()
    return created


def mark_demo_products_legacy(session: Session) -> int:
    """Marque PMC0001…PMC00xx comme legacy sans renommer ni supprimer."""
    updated = 0
    for product in session.scalars(select(Product)):
        code = product.code or ""
        if len(code) == 7 and code.startswith("PMC") and code[3:].isdigit():
            if not bool(product.is_legacy):
                product.is_legacy = True
                updated += 1
            if product.is_active is None:
                product.is_active = True
    if updated:
        session.flush()
    return updated
