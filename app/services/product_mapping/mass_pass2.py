"""Passe 2 — familles déterministes à volume (panneaux, sacs, câbles).

Réutilise CategoryRule, IdentityModel, pipeline.apply_exact. Aucun écrasement
d'un SupplierProduct déjà rattaché.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, Supplier, SupplierProduct
from app.services.product_mapping import pipeline
from app.services.product_mapping.identity_models import (
    BAGGED_PRODUCT,
    BOARD_PANEL,
    ELECTRICAL_CABLE,
)
from app.services.product_mapping.panneau_mdf_extractor import ExtractionResult
from app.services.product_mapping.primitives.dimensions import extract_dimensions_mm
from app.services.product_mapping.primitives.textutil import fold, parse_number, to_mm
from app.services.product_mapping.rules.base import CategoryRule, register_rule

REASON_NOT_THIS_CATEGORY = "NOT_THIS_CATEGORY"

_TRIPLE_UNIT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*[x×]\s*"
    r"(\d+(?:[.,]\d+)?)\s*(mm|cm|m)\b",
    re.I,
)
_KG = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg\b", re.I)
_LENGTH_M = re.compile(r"(\d+)\s*m(?!m)\b", re.I)
_G_SECTION = re.compile(r"(\d)\s*g\s*(\d+(?:[.,]\d+)?)", re.I)
_X_SECTION = re.compile(r"(\d)\s*x\s*(\d+(?:[.,]\d+)?)\s*mm", re.I)
_MM2 = re.compile(r"(\d+(?:[.,]\d+)?)\s*mm", re.I)
_COLOR_VJ = re.compile(r"vert\s*[/\-]\s*jaune")
_COLORS = (
    ("bleu", re.compile(r"\bbleu\b")),
    ("rouge", re.compile(r"\brouge\b")),
    ("noir", re.compile(r"\bnoir\b")),
    ("blanc", re.compile(r"\bblanc\b")),
    ("marron", re.compile(r"\bmarron\b")),
    ("orange", re.compile(r"\borange\b")),
    ("violet", re.compile(r"\bviolet\b")),
    ("gris", re.compile(r"\bgris\b")),
    ("vert", re.compile(r"\bvert\b")),
    ("jaune", re.compile(r"\bjaune\b")),
)

PASS2_CODES = (
    "VIS_METAUX",
    "BETON_CELLULAIRE",
    "PARPAING",
    "POLYSTYRENE_XPS",
    "GRANULE_BOIS",
    "CIMENT_CEM2",
    "CABLE_ELEC",
)


def canonical_section(raw: str) -> str | None:
    number = parse_number(raw)
    if number is None or number <= 0 or number > 35:
        return None
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def cable_colors(blob: str) -> list[str]:
    """Une seule couleur métier. vert/jaune compte pour une."""
    found: list[str] = []
    work = blob
    match = _COLOR_VJ.search(work)
    if match:
        found.append("vert_jaune")
        work = work[: match.start()] + " " + work[match.end() :]
    for name, pattern in _COLORS:
        if pattern.search(work):
            found.append(name)
    return found


def parse_cable_identity(designation: str) -> dict[str, Any] | None:
    """Identité câble complète, ou None si la désignation n'est pas déterministe."""
    blob = fold(designation)
    compact = blob.replace(" ", "").replace("-", "")
    if any(token in blob for token in ("hifi", "speedfil", "coax", "rj45", "utp", "ftp")):
        return None
    if "h07vu" in compact:
        kind = "h07vu"
    elif re.search(r"\br2v\b", blob):
        kind = "r2v"
    elif "h05vvf" in compact:
        kind = "h05vvf"
    else:
        return None

    conductors: int | None = None
    section: str | None = None
    grouped = _G_SECTION.search(blob)
    crossed = _X_SECTION.search(blob)
    if grouped:
        conductors = int(grouped.group(1))
        section = canonical_section(grouped.group(2))
    elif crossed:
        conductors = int(crossed.group(1))
        section = canonical_section(crossed.group(2))
    else:
        mm = _MM2.search(blob)
        if mm:
            section = canonical_section(mm.group(1))
            conductors = 1 if kind == "h07vu" else None
    if section is None or conductors is None:
        return None
    if kind == "h07vu" and conductors != 1:
        return None
    if not 1 <= conductors <= 5:
        return None

    lengths = [int(value) for value in _LENGTH_M.findall(blob)]
    lengths = [value for value in lengths if 5 <= value <= 500]
    if len(lengths) != 1:
        return None

    colors = cable_colors(blob)
    if len(colors) != 1:
        return None
    return {
        "kind": kind,
        "section_mm2": section,
        "conductors": conductors,
        "length_m": lengths[0],
        "color": colors[0],
    }


def canonical_box_mm(text: str) -> tuple[int, int, int] | None:
    """Triplet L × l × Ép. canonique (décroissant), unité partagée du texte."""
    prepared = re.sub(r"\b(mm|cm|m)\.", r"\1 ", text, flags=re.I)
    length, width, thickness = extract_dimensions_mm(prepared)
    if length and width and thickness and max(length, width, thickness) >= 200:
        return tuple(sorted((int(length), int(width), int(thickness)), reverse=True))  # type: ignore[return-value]
    match = _TRIPLE_UNIT.search(prepared.replace("×", "x"))
    if match:
        unit = match.group(4).lower()
        values = []
        for index in (1, 2, 3):
            number = parse_number(match.group(index))
            if number is None:
                return None
            mm = to_mm(number, unit, bare_small_as_meters=False)
            if mm is None:
                return None
            values.append(int(mm))
        return tuple(sorted(values, reverse=True))  # type: ignore[return-value]
    centimeters = re.findall(r"(\d+(?:[.,]\d+)?)\s*cm\b", fold(prepared))
    if len(centimeters) == 3:
        values = []
        for raw in centimeters:
            number = parse_number(raw)
            if number is None:
                return None
            mm = to_mm(number, "cm", bare_small_as_meters=False)
            if mm is None:
                return None
            values.append(int(mm))
        return tuple(sorted(values, reverse=True))  # type: ignore[return-value]
    return None


def _in_box(dims: tuple[int, int, int], *, lo: tuple[int, int, int], hi: tuple[int, int, int]) -> bool:
    return all(lo[i] <= dims[i] <= hi[i] for i in range(3))


def _not_category() -> ExtractionResult:
    return ExtractionResult(
        category_code=None,
        attributes={},
        confidence=0.0,
        extractor_version="",
        classified=False,
        reason=REASON_NOT_THIS_CATEGORY,
    )


def _result(code: str, version: str, attrs: dict[str, Any], complete: bool) -> ExtractionResult:
    return ExtractionResult(
        category_code=code,
        attributes=attrs,
        confidence=0.9 if complete else 0.4,
        extractor_version=version,
        classified=True,
        reason=None,
    )


def _cellulaire_type(blob: str) -> str | None:
    if "cellulaire" not in blob and "siporex" not in blob and "ytong" not in blob:
        return None
    if any(token in blob for token in ("cheville", "colle", "trepan", "frottoir", "enduit", "lot de", "kit")):
        return None
    if "angle" in blob:
        return "angle"
    if "chainage" in blob or "linteau" in blob:
        return "chainage"
    if "carreau" in blob:
        return "carreau"
    return "bloc"


def _parpaing_type(blob: str) -> str | None:
    if "parpaing" not in blob:
        return None
    if any(token in blob for token in ("crepi", "trepan", "colle", "enduit", "cheville", "lot de", "kit")):
        return None
    if "bancher" in blob:
        return "bancher"
    if "angle" in blob:
        return "angle"
    if "chainage" in blob or "linteau" in blob:
        return "chainage"
    if "creux" in blob:
        return "creux"
    return None


def _is_xps(blob: str) -> bool:
    if "polystyr" not in blob and "xps" not in blob:
        return False
    if "extrud" not in blob and "xps" not in blob:
        return False
    if "panneau" not in blob and "plaque" not in blob:
        return False
    blocked = (
        "colle",
        "corniche",
        "moulure",
        "doublage",
        "dalle",
        "plafond",
        "frottoir",
        "taloche",
        "habillage",
        "rosace",
        "lot de",
        "kit",
    )
    return not any(token in blob for token in blocked)


def _integer_kg(blob: str, *, low: int, high: int) -> int | None:
    found = _KG.findall(blob)
    if len(found) != 1:
        return None
    number = parse_number(found[0])
    if number is None or number != number.to_integral_value():
        return None
    kg = int(number)
    if kg < low or kg > high:
        return None
    return kg


def extract_beton_cellulaire(designation: str, category_path: str | None = None) -> ExtractionResult:
    blob = fold(designation)
    kind = _cellulaire_type(blob)
    if kind is None:
        return _not_category()
    dims = canonical_box_mm(designation)
    ok = dims is not None and _in_box(dims, lo=(400, 150, 40), hi=(700, 400, 300))
    attrs = {
        "type": kind,
        "length_mm": dims[0] if ok else None,
        "width_mm": dims[1] if ok else None,
        "thickness_mm": dims[2] if ok else None,
    }
    return _result("BETON_CELLULAIRE", "beton_cellulaire.v1", attrs, ok)


def extract_parpaing(designation: str, category_path: str | None = None) -> ExtractionResult:
    blob = fold(designation)
    kind = _parpaing_type(blob)
    if kind is None:
        return _not_category()
    dims = canonical_box_mm(designation)
    ok = dims is not None and _in_box(dims, lo=(400, 150, 50), hi=(650, 300, 250))
    attrs = {
        "type": kind,
        "length_mm": dims[0] if ok else None,
        "width_mm": dims[1] if ok else None,
        "thickness_mm": dims[2] if ok else None,
    }
    return _result("PARPAING", "parpaing.v1", attrs, ok)


def extract_xps(designation: str, category_path: str | None = None) -> ExtractionResult:
    if not _is_xps(fold(designation)):
        return _not_category()
    dims = canonical_box_mm(designation)
    ok = dims is not None and _in_box(dims, lo=(800, 400, 10), hi=(3000, 1500, 200))
    attrs = {
        "type": "xps",
        "length_mm": dims[0] if ok else None,
        "width_mm": dims[1] if ok else None,
        "thickness_mm": dims[2] if ok else None,
    }
    return _result("POLYSTYRENE_XPS", "polystyrene_xps.v1", attrs, ok)


def extract_granule_bois(designation: str, category_path: str | None = None) -> ExtractionResult:
    blob = fold(designation)
    if "granul" not in blob or "bois" not in blob:
        return _not_category()
    if any(token in blob for token in ("sable", "piscine", "ramonage", "deshumid", "silice", "lot de")):
        return _not_category()
    kg = _integer_kg(blob, low=5, high=20)
    return _result(
        "GRANULE_BOIS",
        "granule_bois.v1",
        {"type": "granule_bois", "weight_kg": kg},
        kg is not None,
    )


def extract_ciment_cem2(designation: str, category_path: str | None = None) -> ExtractionResult:
    blob = fold(designation)
    compact = blob.replace(" ", "")
    if "ciment" not in blob:
        return _not_category()
    if not re.search(r"32[,.]5|\bcpj\b", blob):
        return _not_category()
    if "cemii" not in compact and "cpj" not in blob:
        return _not_category()
    if any(token in blob for token in ("colorant", "joint", "prompt", "epoxy", "mortier", "colle", "blanc")):
        return _not_category()
    kg = _integer_kg(blob, low=10, high=40)
    return _result(
        "CIMENT_CEM2",
        "ciment_cem2.v1",
        {"type": "ciment_cem2_325", "weight_kg": kg},
        kg is not None,
    )


def extract_cable(designation: str, category_path: str | None = None) -> ExtractionResult:
    blob = fold(designation)
    compact = blob.replace(" ", "").replace("-", "")
    if "h07vu" not in compact and not re.search(r"\br2v\b", blob) and "h05vvf" not in compact:
        return _not_category()
    identity = parse_cable_identity(designation)
    attrs = identity or {
        "kind": None,
        "section_mm2": None,
        "conductors": None,
        "length_m": None,
        "color": None,
    }
    return _result("CABLE_ELEC", "cable_elec.v1", attrs, identity is not None)


def _board_defs(types: list[str]) -> tuple[dict[str, Any], ...]:
    return (
        {"key": "type", "data_type": "enum", "required": True, "match_role": "identity", "enum_values": types},
        {"key": "length_mm", "data_type": "int", "required": True, "unit": "mm", "match_role": "identity"},
        {"key": "width_mm", "data_type": "int", "required": True, "unit": "mm", "match_role": "identity"},
        {"key": "thickness_mm", "data_type": "int", "required": True, "unit": "mm", "match_role": "identity"},
    )


BETON_CELLULAIRE_RULE = register_rule(
    CategoryRule(
        code="BETON_CELLULAIRE",
        category_name="Béton cellulaire",
        identity=BOARD_PANEL,
        pmc_code_prefixes=("PMC-CELLULAIRE-",),
        pmc_subcategory_equals=("Béton cellulaire",),
        designation_ilike=("cellulaire", "siporex", "ytong"),
        attribute_defs=_board_defs(["bloc", "angle", "chainage", "carreau"]),
        reference_unit_default="pièce",
        algorithm_version="beton_cellulaire_match.v1",
        extractor_version="beton_cellulaire.v1",
        extract=extract_beton_cellulaire,
    )
)

PARPAING_RULE = register_rule(
    CategoryRule(
        code="PARPAING",
        category_name="Parpaing",
        identity=BOARD_PANEL,
        pmc_code_prefixes=("PMC-PARPAING-",),
        pmc_subcategory_equals=("Parpaing",),
        designation_ilike=("parpaing",),
        attribute_defs=_board_defs(["creux", "angle", "chainage", "bancher"]),
        reference_unit_default="pièce",
        algorithm_version="parpaing_match.v1",
        extractor_version="parpaing.v1",
        extract=extract_parpaing,
    )
)

POLYSTYRENE_XPS_RULE = register_rule(
    CategoryRule(
        code="POLYSTYRENE_XPS",
        category_name="Polystyrène extrudé",
        identity=BOARD_PANEL,
        pmc_code_prefixes=("PMC-XPS-",),
        pmc_subcategory_equals=("Polystyrène extrudé",),
        designation_ilike=("polystyrène", "polystyrene", "xps"),
        attribute_defs=_board_defs(["xps"]),
        reference_unit_default="pièce",
        algorithm_version="polystyrene_xps_match.v1",
        extractor_version="polystyrene_xps.v1",
        extract=extract_xps,
    )
)

GRANULE_BOIS_RULE = register_rule(
    CategoryRule(
        code="GRANULE_BOIS",
        category_name="Granulés de bois",
        identity=BAGGED_PRODUCT,
        pmc_code_prefixes=("PMC-GRANULE-BOIS-",),
        pmc_subcategory_equals=("Granulés de bois",),
        designation_ilike=("granulé", "granule", "granulés", "granules"),
        attribute_defs=(
            {"key": "type", "data_type": "enum", "required": True, "match_role": "identity", "enum_values": ["granule_bois"]},
            {"key": "weight_kg", "data_type": "int", "required": True, "unit": "kg", "match_role": "identity"},
        ),
        reference_unit_default="sac",
        algorithm_version="granule_bois_match.v1",
        extractor_version="granule_bois.v1",
        extract=extract_granule_bois,
    )
)

CIMENT_CEM2_RULE = register_rule(
    CategoryRule(
        code="CIMENT_CEM2",
        category_name="Ciment CEM II 32,5",
        identity=BAGGED_PRODUCT,
        pmc_code_prefixes=("PMC-CIMENT-CEM2-",),
        pmc_subcategory_equals=("Ciment CEM II 32,5",),
        designation_ilike=("ciment",),
        attribute_defs=(
            {
                "key": "type",
                "data_type": "enum",
                "required": True,
                "match_role": "identity",
                "enum_values": ["ciment_cem2_325"],
            },
            {"key": "weight_kg", "data_type": "int", "required": True, "unit": "kg", "match_role": "identity"},
        ),
        reference_unit_default="sac",
        algorithm_version="ciment_cem2_match.v1",
        extractor_version="ciment_cem2.v1",
        extract=extract_ciment_cem2,
    )
)

CABLE_ELEC_RULE = register_rule(
    CategoryRule(
        code="CABLE_ELEC",
        category_name="Câble électrique",
        identity=ELECTRICAL_CABLE,
        pmc_code_prefixes=("PMC-CABLE-",),
        pmc_subcategory_equals=("Câble électrique",),
        designation_ilike=("h07", "r2v", "h05vv"),
        attribute_defs=(
            {
                "key": "kind",
                "data_type": "enum",
                "required": True,
                "match_role": "identity",
                "enum_values": ["h07vu", "r2v", "h05vvf"],
            },
            {"key": "section_mm2", "data_type": "string", "required": True, "match_role": "identity"},
            {"key": "conductors", "data_type": "int", "required": True, "match_role": "identity"},
            {"key": "length_m", "data_type": "int", "required": True, "unit": "m", "match_role": "identity"},
            {"key": "color", "data_type": "string", "required": True, "match_role": "identity"},
        ),
        reference_unit_default="pièce",
        algorithm_version="cable_elec_match.v1",
        extractor_version="cable_elec.v1",
        extract=extract_cable,
    )
)


def _seed_board(session: Session, category_code: str, meta: dict[str, str]) -> dict[str, Any]:
    rule = pipeline_rule(category_code)
    run = pipeline.run_category(session, rule, persist=False, all_candidates=True, limit=None)
    identities: set[tuple[str, int, int, int]] = set()
    for item in run.items:
        if not item.classified:
            continue
        kind = item.attrs.get("type")
        length, width, thickness = (
            item.attrs.get("length_mm"),
            item.attrs.get("width_mm"),
            item.attrs.get("thickness_mm"),
        )
        if kind is None or length is None or width is None or thickness is None:
            continue
        identities.add((str(kind), int(length), int(width), int(thickness)))
    created = 0
    skipped = 0
    codes: list[str] = []
    for kind, length, width, thickness in sorted(identities):
        code = f"{meta['prefix']}{kind.upper()}-{length}X{width}X{thickness}"
        if session.scalar(select(Product).where(Product.code == code)):
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=f"{meta['label']} {kind} {length} × {width} × {thickness} mm",
                category=meta["category"],
                subcategory=meta["subcategory"],
                reference_unit="pièce",
                description=f"{meta['label']} — type {kind}, L×l×Ép.",
                attributes={
                    "type": kind,
                    "length_mm": length,
                    "width_mm": width,
                    "thickness_mm": thickness,
                },
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        codes.append(code)
    if created:
        session.flush()
    return {
        "category": category_code,
        "identities_found": len(identities),
        "created": created,
        "skipped_existing": skipped,
        "created_codes": codes,
    }


def _seed_bag(session: Session, category_code: str, meta: dict[str, str]) -> dict[str, Any]:
    rule = pipeline_rule(category_code)
    run = pipeline.run_category(session, rule, persist=False, all_candidates=True, limit=None)
    weights: set[int] = set()
    for item in run.items:
        if not item.classified or item.attrs.get("weight_kg") is None:
            continue
        if item.attrs.get("type") != meta["type"]:
            continue
        weights.add(int(item.attrs["weight_kg"]))
    created = 0
    skipped = 0
    codes: list[str] = []
    for kg in sorted(weights):
        code = f"{meta['prefix']}{kg}KG"
        if session.scalar(select(Product).where(Product.code == code)):
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=f"{meta['label']} {kg} kg",
                category=meta["category"],
                subcategory=meta["subcategory"],
                reference_unit="sac",
                description=f"{meta['label']} — sac {kg} kg.",
                attributes={"type": meta["type"], "weight_kg": kg},
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        codes.append(code)
    if created:
        session.flush()
    return {
        "category": category_code,
        "identities_found": len(weights),
        "created": created,
        "skipped_existing": skipped,
        "created_codes": codes,
    }


def _seed_cable(session: Session) -> dict[str, Any]:
    rule = pipeline_rule("CABLE_ELEC")
    run = pipeline.run_category(session, rule, persist=False, all_candidates=True, limit=None)
    identities: set[tuple[str, str, int, int, str]] = set()
    for item in run.items:
        if not item.classified:
            continue
        kind = item.attrs.get("kind")
        section = item.attrs.get("section_mm2")
        conductors = item.attrs.get("conductors")
        length = item.attrs.get("length_m")
        color = item.attrs.get("color")
        if None in (kind, section, conductors, length, color):
            continue
        identities.add((str(kind), str(section), int(conductors), int(length), str(color)))
    created = 0
    skipped = 0
    codes: list[str] = []
    for kind, section, conductors, length, color in sorted(identities):
        section_code = section.replace(".", "P")
        code = f"PMC-CABLE-{kind.upper()}-{conductors}G{section_code}-{color.upper()}-{length}M"
        if len(code) > 64:
            skipped += 1
            continue
        if session.scalar(select(Product).where(Product.code == code)):
            skipped += 1
            continue
        session.add(
            Product(
                code=code,
                name=f"Câble {kind.upper()} {conductors}G{section} {color} {length} m",
                category="Électricité",
                subcategory="Câble électrique",
                reference_unit="pièce",
                description=f"Câble {kind} {conductors}×{section} mm² {color}, couronne {length} m.",
                attributes={
                    "kind": kind,
                    "section_mm2": section,
                    "conductors": conductors,
                    "length_m": length,
                    "color": color,
                },
                is_active=True,
                is_legacy=False,
            )
        )
        created += 1
        codes.append(code)
    if created:
        session.flush()
    return {
        "category": "CABLE_ELEC",
        "identities_found": len(identities),
        "created": created,
        "skipped_existing": skipped,
        "created_codes": codes,
    }


_BOARD_META = {
    "BETON_CELLULAIRE": {
        "prefix": "PMC-CELLULAIRE-",
        "label": "Béton cellulaire",
        "category": "Maçonnerie",
        "subcategory": "Béton cellulaire",
    },
    "PARPAING": {
        "prefix": "PMC-PARPAING-",
        "label": "Parpaing",
        "category": "Maçonnerie",
        "subcategory": "Parpaing",
    },
    "POLYSTYRENE_XPS": {
        "prefix": "PMC-XPS-",
        "label": "Polystyrène extrudé",
        "category": "Isolation",
        "subcategory": "Polystyrène extrudé",
    },
}
_BAG_META = {
    "GRANULE_BOIS": {
        "prefix": "PMC-GRANULE-BOIS-",
        "label": "Granulés de bois",
        "category": "Chauffage",
        "subcategory": "Granulés de bois",
        "type": "granule_bois",
    },
    "CIMENT_CEM2": {
        "prefix": "PMC-CIMENT-CEM2-",
        "label": "Ciment CEM II 32,5",
        "category": "Maçonnerie",
        "subcategory": "Ciment CEM II 32,5",
        "type": "ciment_cem2_325",
    },
}


def pipeline_rule(code: str) -> CategoryRule:
    from app.services.product_mapping.rules.base import get_rule

    return get_rule(code)


def seed_pass2_family(session: Session, category_code: str) -> dict[str, Any]:
    if category_code == "VIS_METAUX":
        from app.services.product_mapping.mass_batch import seed_dimensional

        return seed_dimensional(session, category_code)
    if category_code in _BOARD_META:
        return _seed_board(session, category_code, _BOARD_META[category_code])
    if category_code in _BAG_META:
        return _seed_bag(session, category_code, _BAG_META[category_code])
    if category_code == "CABLE_ELEC":
        return _seed_cable(session)
    raise KeyError(category_code)


def brico_counts(session: Session) -> dict[str, int]:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == "BRICO_DEPOT"))
    total = int(
        session.scalar(
            select(func.count()).select_from(SupplierProduct).where(SupplierProduct.supplier_id == supplier_id)
        )
        or 0
    )
    mapped = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.product_id.is_not(None),
            )
        )
        or 0
    )
    manual = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.correction_source == "manual",
            )
        )
        or 0
    )
    exact_rule = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(
                SupplierProduct.supplier_id == supplier_id,
                SupplierProduct.correction_source == "exact_rule",
            )
        )
        or 0
    )
    return {
        "total": total,
        "mapped": mapped,
        "unmapped": total - mapped,
        "manual": manual,
        "exact_rule": exact_rule,
    }


def _snapshot_mapped(session: Session) -> dict[int, tuple[int, str | None]]:
    rows = session.execute(
        select(SupplierProduct.id, SupplierProduct.product_id, SupplierProduct.correction_source).where(
            SupplierProduct.product_id.is_not(None)
        )
    ).all()
    return {int(row[0]): (int(row[1]), row[2]) for row in rows}


def _assert_preserved(session: Session, before: dict[int, tuple[int, str | None]]) -> None:
    if not before:
        return
    rows = session.execute(
        select(SupplierProduct.id, SupplierProduct.product_id, SupplierProduct.correction_source).where(
            SupplierProduct.id.in_(list(before))
        )
    ).all()
    found = {int(row[0]): (row[1], row[2]) for row in rows}
    for sp_id, (product_id, source) in before.items():
        current = found.get(sp_id)
        if current is None or current[0] != product_id:
            raise RuntimeError(f"mapping existant modifié: sp={sp_id}")
        if source in ("manual", "exact_rule") and current[1] != source:
            raise RuntimeError(f"correction_source modifié: sp={sp_id}")


_LEFTOVER_BUCKETS: tuple[tuple[str, str, str], ...] = (
    ("peinture", r"\bpeinture\b", "teinte, finition, usage et volume non déterministes"),
    ("meuble", r"\bmeuble\b|\bfacade\b", "menuiserie d'agencement, identité non stable"),
    ("menuiserie", r"\bporte\b|\bfenetre\b|\bvolet\b|\bbloc[- ]porte\b", "menuiserie sur mesure / gamme, laissée de côté"),
    ("carrelage", r"carrelage|gres cere|faience", "décor et coloris font l'identité, dimensions insuffisantes"),
    ("stratifie", r"stratif", "décor commercial, pas seulement l'épaisseur"),
    ("lame_sol", r"\blame\b", "lame PVC / terrasse : décor et profil non séparables"),
    ("robinetterie", r"robinet|mitigeur|melangeur", "gamme et finition, pas une identité dimensionnelle"),
    ("radiateur", r"\bradiateur\b", "puissance, entraxe et design non stables"),
    ("lot_kit", r"\blot de\b|\bkit de\b|\bboite de\b", "assortiment, pas un produit unitaire"),
    ("enduit", r"\benduit\b", "rebouchage / lissage / façade / pâte / poudre se croisent"),
    ("mortier_colle", r"\bmortier\b|\bcolle\b", "chimie et usage se croisent à poids égal"),
    ("appareillage", r"\bprise\b|\binterrupteur\b|\bva[- ]et[- ]vient\b", "gamme (Odace, Dooxie…) et couleur"),
    ("plinthe", r"\bplinthe\b", "décor et matière mélangés"),
    ("tube_raccord", r"\btube\b|\braccord\b|\btuyau\b", "matière, diamètre et conditionnement non univoques"),
    ("outillage", r"\bforet\b|\bdisque\b|\blame de scie\b", "consommable d'outillage, hors identité PMC chantier"),
    ("serrurerie", r"\bserrure\b|\bcylindre\b", "modèle de serrure non normalisable"),
    ("vis_residue", r"\bvis\b", "vis hors famille déjà couverte, Ø×L absent ou ambigu"),
    ("cheville_residue", r"\bcheville\b", "cheville hors nylon / métal déjà couverts"),
    ("isolation_residue", r"\blaine\b|\bisolant\b", "lambda, R et parement non tous présents"),
    ("panneau_residue", r"\bpanneau\b|\bplaque\b", "hors OSB / CP / agglo / XPS / plâtre déjà couverts"),
)


def leftover_families(session: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == "BRICO_DEPOT"))
    rows = session.execute(
        select(SupplierProduct.designation).where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.product_id.is_(None),
        )
    ).all()
    compiled = [(name, re.compile(pattern), reason) for name, pattern, reason in _LEFTOVER_BUCKETS]
    counts = {name: 0 for name, _, _ in _LEFTOVER_BUCKETS}
    reasons = {name: reason for name, _, reason in _LEFTOVER_BUCKETS}
    other = 0
    for (designation,) in rows:
        blob = fold(designation or "")
        matched = False
        for name, pattern, _reason in compiled:
            if pattern.search(blob):
                counts[name] += 1
                matched = True
                break
        if not matched:
            other += 1
    report = [
        {"famille": name, "sp": count, "blocage": reasons[name]}
        for name, count in counts.items()
        if count
    ]
    report.append({"famille": "autre_retail", "sp": other, "blocage": "pas de famille métier déterministe"})
    report.sort(key=lambda item: -item["sp"])
    return report[:limit]


def run_pass2(session: Session, *, apply: bool = True) -> dict[str, Any]:
    import app.services.product_mapping.rules  # noqa: F401

    from app.services.product_mapping.mass_batch import (
        apply_if_clean,
        audit_exact_matches,
        dry_run_summary,
    )

    before = brico_counts(session)
    snapshot = _snapshot_mapped(session)
    products_before = int(session.scalar(select(func.count()).select_from(Product)) or 0)
    report: dict[str, Any] = {"before": before, "families": []}

    for code in PASS2_CODES:
        family: dict[str, Any] = {"category": code}
        family["seed"] = seed_pass2_family(session, code)
        session.flush()
        family["dry_run"] = dry_run_summary(session, code)
        family["exact_audit"] = audit_exact_matches(session, code)
        if apply and family["exact_audit"]["mismatch_count"] == 0:
            family["apply"] = apply_if_clean(session, code)
            session.flush()
            family["seed_second"] = seed_pass2_family(session, code)
            family["apply_second"] = apply_if_clean(session, code)
        elif apply:
            family["apply"] = {
                "applied": 0,
                "skipped_reason": "exact_mismatch",
                "audit": family["exact_audit"],
            }
            family["seed_second"] = {"created": None}
            family["apply_second"] = {"applied": None}
        report["families"].append(family)

    if apply:
        _assert_preserved(session, snapshot)
        for family in report["families"]:
            second_seed = (family.get("seed_second") or {}).get("created")
            second_apply = (family.get("apply_second") or {}).get("applied")
            if second_seed not in (0, None) or second_apply not in (0, None):
                session.rollback()
                raise RuntimeError(f"passe non idempotente: {family['category']}")
        session.commit()
    else:
        session.rollback()

    after = brico_counts(session)
    products_after = int(session.scalar(select(func.count()).select_from(Product)) or 0)
    report["after"] = after
    report["products_before"] = products_before
    report["products_after"] = products_after
    report["pmc_created"] = products_after - products_before
    report["leftover"] = leftover_families(session)
    statuses = {"exact": 0, "review": 0, "insufficient_data": 0, "no_pmc_product": 0, "ambiguous": 0}
    for family in report["families"]:
        dry = family.get("dry_run") or {}
        for key in statuses:
            statuses[key] += int(dry.get(key) or 0)
    report["statuses_during_pass"] = statuses
    return report
