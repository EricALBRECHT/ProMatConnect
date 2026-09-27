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
    # V1.2 — variantes manquantes identifiées par analyse PLAQUE_PLATRE
    (
        "PMC-BA13-HYDRO-2500X600",
        "Plaque BA13 hydrofuge 2500 × 600 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 600,
            "thickness_mm": 13,
            "type": "hydrofuge",
            "surface_m2": 1.5,
        },
        "Plaque BA13 hydrofuge format étroit — quantité = pièces (surface technique 1,5 m²).",
    ),
    (
        "PMC-BA13-PHONI-2500X1200",
        "Plaque BA13 phonique 2500 × 1200 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "phonique",
            "surface_m2": 3.0,
        },
        "Plaque BA13 phonique / acoustique — quantité = pièces (surface technique 3,0 m²).",
    ),
    (
        "PMC-BA10-STD-2500X1200",
        "Plaque BA10 standard 2500 × 1200 × 10 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 10,
            "type": "standard",
            "surface_m2": 3.0,
        },
        "Plaque BA10 standard 10 mm — distincte de BA13 ; quantité = pièces (surface 3,0 m²).",
    ),
    (
        "PMC-BA13-HYDRO-1250X600",
        "Plaque BA13 hydrofuge 1250 × 600 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 1250,
            "width_mm": 600,
            "thickness_mm": 13,
            "type": "hydrofuge",
            "surface_m2": 0.75,
        },
        "Plaque BA13 hydrofuge demi-format — quantité = pièces (surface technique 0,75 m²).",
    ),
    (
        "PMC-BA13-FEU-2500X1200",
        "Plaque BA13 feu / résistante au feu 2500 × 1200 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 2500,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "feu",
            "surface_m2": 3.0,
        },
        "Plaque BA13 résistante au feu — distincte du standard ; quantité = pièces (3,0 m²).",
    ),
    (
        "PMC-BA13-STD-1250X600",
        "Plaque BA13 standard 1250 × 600 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 1250,
            "width_mm": 600,
            "thickness_mm": 13,
            "type": "standard",
            "surface_m2": 0.75,
        },
        "Plaque BA13 standard demi-format — quantité = pièces (surface technique 0,75 m²).",
    ),
    # V1.4 — format 3,00 m observé Brico (SP 17113)
    (
        "PMC-BA13-STD-3000X1200",
        "Plaque BA13 standard 3000 × 1200 × 13 mm",
        "Plâtrerie",
        "Plaques de plâtre",
        "pièce",
        {
            "length_mm": 3000,
            "width_mm": 1200,
            "thickness_mm": 13,
            "type": "standard",
            "surface_m2": 3.6,
        },
        "Plaque BA13 standard 3,00 m — quantité = pièces (surface technique 3,6 m²).",
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
    # V1.1 OSSATURE — variantes manquantes confirmées dry-run
    (
        "PMC-MONTANT-M70-2500",
        "Montant M70 ~2,50 m",
        "Ossature",
        "Montants",
        "pièce",
        {"profile": "M70", "length_mm": 2500},
        "Montant M70 ~2,50 m — distinct du M48. Quantité = pièces.",
    ),
    (
        "PMC-FOURRURE-F45-5300",
        "Fourrure F45 5,30 m",
        "Ossature",
        "Fourrures",
        "pièce",
        {"profile": "F45", "length_mm": 5300},
        "Fourrure F45 5,30 m — distincte du 3 m. Quantité = pièces.",
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
    # VIS_PLACO — variantes Brico sûres (hors TTPC 8×… non confirmés)
    (
        "PMC-VIS-PLACO-35X45",
        "Vis plaque de plâtre 3,5 × 45 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 45, "type": "placo"},
        "Vis placo 3,5×45 — distincte des 3,5×25/35/55.",
    ),
    (
        "PMC-VIS-PLACO-35X55",
        "Vis plaque de plâtre 3,5 × 55 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 55, "type": "placo"},
        "Vis placo 3,5×55 — distincte des 3,5×25/35/45.",
    ),
    (
        "PMC-VIS-PLACO-42X70",
        "Vis plaque de plâtre 4,2 × 70 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.2, "length_mm": 70, "type": "placo"},
        "Vis placo 4,2×70 — distincte des autres 4,2.",
    ),
    (
        "PMC-VIS-PLACO-42X80",
        "Vis plaque de plâtre 4,2 × 80 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.2, "length_mm": 80, "type": "placo"},
        "Vis placo 4,2×80 — distincte des autres 4,2.",
    ),
    (
        "PMC-VIS-PLACO-42X90",
        "Vis plaque de plâtre 4,2 × 90 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.2, "length_mm": 90, "type": "placo"},
        "Vis placo 4,2×90 — distincte des autres 4,2.",
    ),
    (
        "PMC-VIS-PLACO-48X70",
        "Vis plaque de plâtre 4,8 × 70 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.8, "length_mm": 70, "type": "placo"},
        "Vis placo 4,8×70 — distincte des autres 4,8.",
    ),
    (
        "PMC-VIS-PLACO-48X90",
        "Vis plaque de plâtre 4,8 × 90 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.8, "length_mm": 90, "type": "placo"},
        "Vis placo 4,8×90 — distincte des autres 4,8.",
    ),
    (
        "PMC-VIS-PLACO-48X100",
        "Vis plaque de plâtre 4,8 × 100 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.8, "length_mm": 100, "type": "placo"},
        "Vis placo 4,8×100 — distincte des autres 4,8.",
    ),
    (
        "PMC-VIS-PLACO-48X110",
        "Vis plaque de plâtre 4,8 × 110 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.8, "length_mm": 110, "type": "placo"},
        "Vis placo 4,8×110 — distincte des autres 4,8.",
    ),
    (
        "PMC-VIS-PLACO-48X120",
        "Vis plaque de plâtre 4,8 × 120 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.8, "length_mm": 120, "type": "placo"},
        "Vis placo 4,8×120 — distincte des autres 4,8.",
    ),
    (
        "PMC-VIS-PLACO-48X140",
        "Vis plaque de plâtre 4,8 × 140 mm",
        "Fixation",
        "Vis placo",
        "pièce",
        {"diameter_mm": 4.8, "length_mm": 140, "type": "placo"},
        "Vis placo 4,8×140 — distincte des autres 4,8.",
    ),
    # VIS_AGGLO — identités Brico (diamètre × longueur, type agglo)
    (
        "PMC-VIS-AGGLO-40X40",
        "Vis agglo 4 × 40 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.0, "length_mm": 40, "type": "agglo"},
        "Vis agglo 4×40 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-50X40",
        "Vis agglo 5 × 40 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 5.0, "length_mm": 40, "type": "agglo"},
        "Vis agglo 5×40 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-30X20",
        "Vis agglo 3 × 20 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.0, "length_mm": 20, "type": "agglo"},
        "Vis agglo 3×20 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-35X20",
        "Vis agglo 3,5 × 20 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 20, "type": "agglo"},
        "Vis agglo 3,5×20 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-45X40",
        "Vis agglo 4,5 × 40 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.5, "length_mm": 40, "type": "agglo"},
        "Vis agglo 4,5×40 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-50X30",
        "Vis agglo 5 × 30 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 5.0, "length_mm": 30, "type": "agglo"},
        "Vis agglo 5×30 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X50",
        "Vis agglo 6 × 50 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 50, "type": "agglo"},
        "Vis agglo 6×50 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-50X50",
        "Vis agglo 5 × 50 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 5.0, "length_mm": 50, "type": "agglo"},
        "Vis agglo 5×50 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-50X70",
        "Vis agglo 5 × 70 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 5.0, "length_mm": 70, "type": "agglo"},
        "Vis agglo 5×70 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X60",
        "Vis agglo 6 × 60 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 60, "type": "agglo"},
        "Vis agglo 6×60 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-26X50",
        "Vis agglo 2,65 × 50 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 2.65, "length_mm": 50, "type": "agglo"},
        "Vis agglo 2,65×50 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-30X25",
        "Vis agglo 3 × 25 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.0, "length_mm": 25, "type": "agglo"},
        "Vis agglo 3×25 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-35X25",
        "Vis agglo 3,5 × 25 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 25, "type": "agglo"},
        "Vis agglo 3,5×25 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-40X25",
        "Vis agglo 4 × 25 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.0, "length_mm": 25, "type": "agglo"},
        "Vis agglo 4×25 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-40X30",
        "Vis agglo 4 × 30 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.0, "length_mm": 30, "type": "agglo"},
        "Vis agglo 4×30 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-40X60",
        "Vis agglo 4 × 60 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.0, "length_mm": 60, "type": "agglo"},
        "Vis agglo 4×60 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-45X25",
        "Vis agglo 4,5 × 25 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.5, "length_mm": 25, "type": "agglo"},
        "Vis agglo 4,5×25 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-45X30",
        "Vis agglo 4,5 × 30 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.5, "length_mm": 30, "type": "agglo"},
        "Vis agglo 4,5×30 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X80",
        "Vis agglo 6 × 80 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 80, "type": "agglo"},
        "Vis agglo 6×80 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X90",
        "Vis agglo 6 × 90 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 90, "type": "agglo"},
        "Vis agglo 6×90 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X100",
        "Vis agglo 6 × 100 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 100, "type": "agglo"},
        "Vis agglo 6×100 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-30X12",
        "Vis agglo 3 × 12 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.0, "length_mm": 12, "type": "agglo"},
        "Vis agglo 3×12 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-35X30",
        "Vis agglo 3,5 × 30 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 30, "type": "agglo"},
        "Vis agglo 3,5×30 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-35X40",
        "Vis agglo 3,5 × 40 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.5, "length_mm": 40, "type": "agglo"},
        "Vis agglo 3,5×40 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-38X75",
        "Vis agglo 3,75 × 75 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 3.75, "length_mm": 75, "type": "agglo"},
        "Vis agglo 3,75×75 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-40X16",
        "Vis agglo 4 × 16 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.0, "length_mm": 16, "type": "agglo"},
        "Vis agglo 4×16 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-40X20",
        "Vis agglo 4 × 20 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.0, "length_mm": 20, "type": "agglo"},
        "Vis agglo 4×20 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-40X50",
        "Vis agglo 4 × 50 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.0, "length_mm": 50, "type": "agglo"},
        "Vis agglo 4×50 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-45X20",
        "Vis agglo 4,5 × 20 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.5, "length_mm": 20, "type": "agglo"},
        "Vis agglo 4,5×20 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-45X50",
        "Vis agglo 4,5 × 50 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 4.5, "length_mm": 50, "type": "agglo"},
        "Vis agglo 4,5×50 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-50X60",
        "Vis agglo 5 × 60 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 5.0, "length_mm": 60, "type": "agglo"},
        "Vis agglo 5×60 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-50X80",
        "Vis agglo 5 × 80 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 5.0, "length_mm": 80, "type": "agglo"},
        "Vis agglo 5×80 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X40",
        "Vis agglo 6 × 40 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 40, "type": "agglo"},
        "Vis agglo 6×40 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X70",
        "Vis agglo 6 × 70 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 70, "type": "agglo"},
        "Vis agglo 6×70 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X120",
        "Vis agglo 6 × 120 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 120, "type": "agglo"},
        "Vis agglo 6×120 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X160",
        "Vis agglo 6 × 160 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 160, "type": "agglo"},
        "Vis agglo 6×160 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-60X180",
        "Vis agglo 6 × 180 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 6.0, "length_mm": 180, "type": "agglo"},
        "Vis agglo 6×180 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-80X120",
        "Vis agglo 8 × 120 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 8.0, "length_mm": 120, "type": "agglo"},
        "Vis agglo 8×120 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-80X220",
        "Vis agglo 8 × 220 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 8.0, "length_mm": 220, "type": "agglo"},
        "Vis agglo 8×220 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-80X240",
        "Vis agglo 8 × 240 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 8.0, "length_mm": 240, "type": "agglo"},
        "Vis agglo 8×240 — identité diamètre × longueur (type agglo).",
    ),
    (
        "PMC-VIS-AGGLO-80X260",
        "Vis agglo 8 × 260 mm",
        "Fixation",
        "Vis agglo",
        "pièce",
        {"diameter_mm": 8.0, "length_mm": 260, "type": "agglo"},
        "Vis agglo 8×260 — identité diamètre × longueur (type agglo).",
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

_PLAQUE_IDENTITY_KEYS = ("length_mm", "width_mm", "thickness_mm", "type")
_PLAQUE_CODE_PREFIXES = ("PMC-BA10-", "PMC-BA13-", "PMC-BA15-", "PMC-BA18-")


def _plaque_identity(attrs: dict | None) -> tuple | None:
    if not attrs:
        return None
    vals = tuple(attrs.get(k) for k in _PLAQUE_IDENTITY_KEYS)
    if any(v is None for v in vals):
        return None
    return vals


def _find_equivalent_plaque(
    session: Session, attributes: dict, *, exclude_code: str | None = None
) -> Product | None:
    """Product plaque déjà présent avec mêmes dims + type (évite doublon technique)."""
    target = _plaque_identity(attributes)
    if target is None:
        return None
    rows = session.scalars(
        select(Product).where(
            Product.subcategory == "Plaques de plâtre",
            Product.is_active.is_(True),
        )
    ).all()
    for p in rows:
        if exclude_code and p.code == exclude_code:
            continue
        if _plaque_identity(p.attributes) == target:
            return p
    return None


def seed_normalized_catalog(session: Session) -> int:
    """Crée les Product normalisés manquants. Corrige l'unité ossature si besoin.

    Ne touche pas aux Product historiques PMC000x (legacy).
    Idempotent : code existant OU équivalence technique plaque (L/l/Ep/type).
    """
    created = 0
    corrected = 0
    for code, name, category, subcategory, unit, attributes, description in NORMALIZED_PRODUCTS:
        existing = session.scalar(select(Product).where(Product.code == code))
        if existing is None:
            # Doublon technique plaque (autre code, mêmes attributs identity)
            if subcategory == "Plaques de plâtre":
                twin = _find_equivalent_plaque(session, attributes)
                if twin is not None:
                    continue
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
        if code.startswith(
            ("PMC-RAIL-", "PMC-MONTANT-", "PMC-FOURRURE-", *_PLAQUE_CODE_PREFIXES)
        ):
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
