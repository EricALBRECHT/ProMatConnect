"""Rapproche Gedimat spécifique ↔ PMC déjà porté par BRICO_DEPOT.

Réutilise CategoryRule, les extracteurs et le matcher générique.
Les properties Gedimat complètent l'identité. Le nom seul ne suffit pas.
Un rattachement n'a lieu que si un seul PMC Brico reste compatible
et qu'aucune caractéristique essentielle ne se contredit.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, Supplier, SupplierProduct
from app.models.product_mapping import CORRECTION_SOURCE_EQUIV, CORRECTION_SOURCE_SPECIFIC
from app.services.gedimat_catalog_import import FEATURE_CATEGORY
from app.services.product_mapping.feature_set import FeatureSet, features_from_attrs
from app.services.product_mapping.generic_matcher import (
    REASON_EXACT,
    best_match,
)
from app.services.product_mapping.primitives.textutil import fold, parse_number, to_mm
from app.services.product_mapping.rules import get_rule, registered_codes
from app.services.product_mapping.rules.base import CategoryRule
from app.models.product_mapping import SupplierProductFeature

_MEASURE_RE = re.compile(
    r"(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>mm|cm|m)?\b",
    re.I,
)
_SYNONYM_RULES: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (re.compile(r"\btf\b"), "tete fraisee", "vis"),
    (re.compile(r"\btx\b"), "torx", None),
    (re.compile(r"\bba\s*13\b"), "plaque de platre 13 mm", None),
    (re.compile(r"\bcp\b"), "contreplaque", "panneau"),
    (re.compile(r"\bagglo\b"), "agglomere", None),
    (re.compile(r"\bgalva\b"), "galvanise", None),
    (re.compile(r"\binox\b"), "acier inoxydable", None),
)
_VARIANT_RE = (
    re.compile(r"isolation[^\d]{0,24}(\d+)\s*mm"),
    re.compile(r"\b(male|femelle)\b"),
    re.compile(r"\b(\d+)\s*vantaux?\b"),
    re.compile(r"\b(carrelage|multi[\s-]?usage|a eau|jante continue)\b"),
)
_DRIVE = {
    "tx": "torx",
    "torx": "torx",
    "pz": "pozidriv",
    "pozidriv": "pozidriv",
    "ph": "philips",
    "philips": "philips",
    "phillips": "philips",
}
_SECONDARY = {
    "DIMENSIONAL_FASTENER": ("head", "drive", "finish", "material"),
    "BOARD_PANEL": ("hydrofuge", "fire_resistant", "acoustic"),
    "LINEAR_PROFILE": ("material",),
    "BAGGED_PRODUCT": ("usage",),
    "ELECTRICAL_CABLE": ("material",),
}


@dataclass
class LinkExample:
    family: str
    level: str
    gedimat_name: str
    gedimat_properties: dict
    brico_name: str
    brico_features: dict
    reason: str


@dataclass
class EquivalenceReport:
    linked: int = 0
    complete: int = 0
    strong: int = 0
    reference: int = 0
    ambiguous: int = 0
    by_family: dict[str, int] = field(default_factory=dict)
    examples: list[LinkExample] = field(default_factory=list)


def measure_to_mm(text: str, *, bare_small_as_meters: bool = False) -> int | None:
    """« 60 mm », « 6 cm », « 0,06 m » → millimètres. None si ce n'est pas une mesure."""
    match = _MEASURE_RE.search(fold(text or ""))
    if match is None:
        return None
    number = parse_number(match.group("n"))
    if number is None:
        return None
    return to_mm(
        number,
        match.group("u"),
        bare_small_as_meters=bare_small_as_meters,
    )


def expand_synonyms(text: str) -> str:
    """Ajoute des formes canoniques. Ne remplace pas le texte d'origine."""
    folded = fold(text or "")
    extra: list[str] = []
    for pattern, canonical, context in _SYNONYM_RULES:
        if context is not None and context not in folded:
            continue
        if pattern.search(folded):
            extra.append(canonical)
    if not extra:
        return text or ""
    return f"{text} {' '.join(extra)}"


def unsupported_variant(gedimat_text: str, brico_text: str) -> bool:
    """Une variante explicite d'un seul côté (isolation, mâle/femelle, usage) bloque."""
    gedimat = fold(gedimat_text or "")
    brico = fold(brico_text or "")
    for pattern in _VARIANT_RE:
        found = pattern.findall(gedimat)
        if not found:
            continue
        if set(found) != set(pattern.findall(brico)):
            return True
    return False


def identity_grounded(identity: tuple, gedimat_text: str, brico_text: str) -> bool:
    """Un format chiffré doit se retrouver dans les deux désignations."""
    gedimat = fold(gedimat_text or "").replace(" ", "")
    brico = fold(brico_text or "").replace(" ", "")
    for value in identity:
        text = str(value)
        if "x" not in text or not text[:1].isdigit():
            continue
        for part in text.split("x"):
            if part not in gedimat or part not in brico:
                return False
    return True


def attributes_conflict(left: dict, right: dict, keys: tuple[str, ...]) -> bool:
    """Vrai si une clé connue des deux côtés porte deux valeurs différentes."""
    for key in keys:
        lv = _norm(left.get(key))
        rv = _norm(right.get(key))
        if lv is None or rv is None:
            continue
        if lv != rv:
            return True
    return False


def reconcile_gedimat(session: Session, *, example_limit: int = 10) -> EquivalenceReport:
    """Une passe sur les fallback Gedimat. Idempotente : ne relit que specific."""
    report = EquivalenceReport()
    brico_id = session.scalar(select(Supplier.id).where(Supplier.name == "BRICO_DEPOT"))
    gedimat_id = session.scalar(select(Supplier.id).where(Supplier.name == "GEDIMAT"))
    if brico_id is None or gedimat_id is None:
        return report

    rules = [get_rule(code) for code in registered_codes()]
    index, attrs_by_rule = _index_brico(session, brico_id, rules)
    rows = session.execute(
        select(SupplierProduct, SupplierProductFeature.attributes, Product.name)
        .join(
            SupplierProductFeature,
            SupplierProductFeature.supplier_product_id == SupplierProduct.id,
        )
        .join(Product, Product.id == SupplierProduct.product_id)
        .where(
            SupplierProduct.supplier_id == gedimat_id,
            SupplierProduct.correction_source == CORRECTION_SOURCE_SPECIFIC,
            SupplierProductFeature.category_code == FEATURE_CATEGORY,
        )
    ).all()

    seen_families: set[str] = set()
    for sp, feature_attrs, current_name in rows:
        decision = _decide(sp, feature_attrs or {}, rules, index, attrs_by_rule)
        if decision is None:
            continue
        if decision.ambiguous:
            report.ambiguous += 1
            continue
        if decision.product_id is None or decision.product_id == sp.product_id:
            continue
        sp.product_id = decision.product_id
        sp.correction_source = CORRECTION_SOURCE_EQUIV
        report.linked += 1
        report.by_family[decision.family] = report.by_family.get(decision.family, 0) + 1
        if decision.level == "complete":
            report.complete += 1
        elif decision.level == "strong":
            report.strong += 1
        else:
            report.reference += 1
        if len(report.examples) < example_limit and decision.family not in seen_families:
            seen_families.add(decision.family)
            report.examples.append(
                LinkExample(
                    family=decision.family,
                    level=decision.level,
                    gedimat_name=sp.designation,
                    gedimat_properties=_main_properties(feature_attrs or {}),
                    brico_name=decision.brico_name or current_name,
                    brico_features=decision.brico_features,
                    reason=decision.reason,
                )
            )
    if report.linked:
        session.flush()
    return report


@dataclass
class _Decision:
    product_id: int | None
    level: str
    family: str
    ambiguous: bool
    reason: str
    brico_name: str | None = None
    brico_features: dict = field(default_factory=dict)


def _decide(sp, feature_attrs: dict, rules, index, attrs_by_rule) -> _Decision | None:
    blob = expand_synonyms(_gedimat_blob(sp, feature_attrs))
    folded = fold(blob)
    proposals: list[_Decision] = []
    for rule in rules:
        if not _rule_applies(rule, folded):
            continue
        extracted, conflict = _extract(rule, blob, feature_attrs.get("properties"))
        if conflict or extracted is None:
            if conflict:
                proposals.append(
                    _Decision(None, "conflict", _family(rule.code), True, "contradiction")
                )
            continue
        identity = _identity(rule, extracted)
        if identity is None:
            continue
        bucket = index.get(rule.code, {}).get(identity, ())
        if not bucket:
            continue
        secondary = _secondary_keys(rule)
        compatible = [
            pid
            for pid in bucket
            if not attributes_conflict(extracted, attrs_by_rule[rule.code].get(pid, {}), secondary)
        ]
        family = _family(rule.code)
        if len(compatible) != 1:
            proposals.append(_Decision(None, "ambiguous", family, True, "plusieurs PMC"))
            continue
        product_id = compatible[0]
        confirmed = best_match(
            rule,
            extracted,
            [(product_id, "", attrs_by_rule[rule.code].get(product_id, {}), None)],
        )
        if confirmed.reason != REASON_EXACT or confirmed.product_id != product_id:
            continue
        brico_name = attrs_by_rule[rule.code].get(product_id, {}).get("_name") or ""
        if unsupported_variant(sp.designation or "", brico_name):
            continue
        if not identity_grounded(identity, sp.designation or "", brico_name):
            continue
        level = "complete" if len(bucket) == 1 else "strong"
        reason = (
            "identité métier complète"
            if level == "complete"
            else "identité essentielle unique après filtres secondaires"
        )
        proposals.append(
            _Decision(
                product_id,
                level,
                family,
                False,
                reason,
                brico_name=attrs_by_rule[rule.code].get(product_id, {}).get("_name"),
                brico_features={
                    key: attrs_by_rule[rule.code].get(product_id, {}).get(key)
                    for key in (*rule.identity_keys, *secondary)
                    if attrs_by_rule[rule.code].get(product_id, {}).get(key) is not None
                },
            )
        )
    actionable = [item for item in proposals if item.product_id and not item.ambiguous]
    if not actionable:
        if any(item.ambiguous for item in proposals):
            return _Decision(None, "ambiguous", proposals[0].family, True, "ambigu ou contradiction")
        return None
    product_ids = {item.product_id for item in actionable}
    if len(product_ids) != 1:
        return _Decision(None, "ambiguous", actionable[0].family, True, "règles en conflit")
    actionable.sort(key=lambda item: 0 if item.level == "complete" else 1)
    return actionable[0]


def _index_brico(session: Session, brico_id: int, rules: list[CategoryRule]):
    index: dict[str, dict[tuple, list[int]]] = defaultdict(lambda: defaultdict(list))
    attrs_by_rule: dict[str, dict[int, dict]] = defaultdict(dict)
    rows = session.execute(
        select(SupplierProduct.designation, SupplierProduct.product_id, Product.name, Product.attributes)
        .join(Product, Product.id == SupplierProduct.product_id)
        .where(
            SupplierProduct.supplier_id == brico_id,
            SupplierProduct.product_id.is_not(None),
        )
    ).all()
    seen: set[tuple[str, int]] = set()
    for designation, product_id, name, product_attrs in rows:
        blob = expand_synonyms(designation or "")
        folded = fold(blob)
        for rule in rules:
            if (rule.code, product_id) in seen:
                continue
            if not _rule_applies(rule, folded):
                continue
            extracted, conflict = _extract(rule, blob, None)
            if conflict or extracted is None:
                continue
            merged = dict(extracted)
            for key, value in (product_attrs or {}).items():
                if key in rule.identity_keys and value is not None:
                    merged[key] = value
                elif merged.get(key) is None and value is not None:
                    merged[key] = value
            _canonicalize(rule, merged)
            identity = _identity(rule, merged)
            if identity is None:
                continue
            seen.add((rule.code, product_id))
            merged["_name"] = name
            attrs_by_rule[rule.code][product_id] = merged
            index[rule.code][identity].append(product_id)
    return index, attrs_by_rule


def _extract(rule: CategoryRule, text: str, properties: Any) -> tuple[dict | None, bool]:
    if rule.extract is None:
        return None, False
    extraction = rule.extract(text)
    if not getattr(extraction, "classified", False):
        return None, False
    attrs = dict(getattr(extraction, "attributes", {}) or {})
    feature_set = FeatureSet(
        category_code=rule.code,
        values=features_from_attrs(attrs),
        classified=True,
        extractor_version=rule.extractor_version,
    )
    flat = feature_set.as_attrs()
    conflict = _overlay_properties(flat, properties, rule)
    if conflict:
        return flat, True
    _canonicalize(rule, flat)
    return flat, False


def _overlay_properties(attrs: dict, properties: Any, rule: CategoryRule) -> bool:
    if not isinstance(properties, dict):
        return False
    wanted = set(rule.identity_keys) | set(_secondary_keys(rule))
    parsed = _parse_properties(properties)
    for key, value in parsed.items():
        if key not in wanted or value is None:
            continue
        current = attrs.get(key)
        if current is None:
            attrs[key] = value
            continue
        if _norm(current) != _norm(value):
            return True
    return False


def _parse_properties(properties: dict) -> dict[str, Any]:
    folded = {fold(str(key)): _join(value) for key, value in properties.items()}
    out: dict[str, Any] = {}
    length = _first(folded, ("longueur de la vis", "longueur du clou", "longueur"))
    if length:
        out["length_mm"] = measure_to_mm(length, bare_small_as_meters=True)
    diameter = _first(folded, ("diametre", "diametre de la vis"))
    if diameter:
        out["diameter_mm"] = _mm_float(diameter)
    thickness = _first(folded, ("epaisseur en mm", "epaisseur", "epaisseur du verre"))
    if thickness:
        bare_meters = "epaisseur en mm" not in folded
        out["thickness_mm"] = measure_to_mm(thickness, bare_small_as_meters=bare_meters)
    width = _first(folded, ("largeur",))
    if width:
        out["width_mm"] = measure_to_mm(width, bare_small_as_meters=True)
    drive = _first(folded, ("empreinte de la vis", "empreinte"))
    if drive:
        out["drive"] = _DRIVE.get(fold(drive).split()[0], fold(drive))
    material = _first(folded, ("matiere",))
    if material:
        out["material"] = _material(material)
    color = _first(folded, ("couleur", "coloris"))
    if color:
        out["color"] = fold(color)
    return {key: value for key, value in out.items() if value is not None}


def _gedimat_blob(sp: SupplierProduct, feature_attrs: dict) -> str:
    parts = [sp.designation or "", str(feature_attrs.get("name_variant") or "")]
    properties = feature_attrs.get("properties")
    if isinstance(properties, dict):
        for value in properties.values():
            parts.append(_join(value))
    return " ".join(part for part in parts if part).strip()


def _identity(rule: CategoryRule, attrs: dict) -> tuple | None:
    """Identité complète. Une valeur vide ou « nr » n'est pas une caractéristique."""
    if not rule.identity_complete(attrs):
        return None
    values = []
    for key in rule.identity_keys:
        value = _norm(attrs.get(key))
        if value in (None, "", "nr"):
            return None
        values.append(value)
    return tuple(values)


def _secondary_keys(rule: CategoryRule) -> tuple[str, ...]:
    model = rule.identity.name if rule.identity is not None else ""
    return tuple(key for key in _SECONDARY.get(model, ()) if key not in rule.identity_keys)


def _rule_applies(rule: CategoryRule, folded: str) -> bool:
    return any(fold(token) in folded for token in rule.designation_ilike)


def _family(code: str) -> str:
    upper = code.upper()
    if upper.startswith("VIS") or "TIREFOND" in upper or "GOUJON" in upper:
        return "VIS"
    if "CHEVILLE" in upper:
        return "CHEVILLE"
    if "BOULON" in upper:
        return "BOULON"
    if "PLAQUE" in upper:
        return "PLAQUE"
    if any(token in upper for token in ("PANNEAU", "OSB", "MDF", "AGGLO")):
        return "PANNEAU"
    if "CIMENT" in upper or "MORTIER" in upper or "ENDUIT" in upper:
        return "CIMENT"
    if "CABLE" in upper or upper.startswith("H07") or "ELEC" in upper:
        return "ELECTRICITE"
    if "OSSATURE" in upper or "RAIL" in upper or "MONTANT" in upper:
        return "OSSATURE"
    return upper


def _main_properties(feature_attrs: dict) -> dict:
    properties = feature_attrs.get("properties")
    if not isinstance(properties, dict):
        return {}
    keep = ("Type de produit", "Matière", "Diamètre", "Longueur de la vis", "Longueur", "Epaisseur en mm", "Largeur", "Empreinte de la vis", "Couleur")
    picked = {key: properties[key] for key in keep if key in properties}
    if picked:
        return picked
    return dict(list(properties.items())[:6])


def _join(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "").strip()


def _first(folded: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        text = folded.get(key)
        if text:
            return text
    return None


def _mm_float(text: str) -> float | None:
    mm = measure_to_mm(text)
    if mm is None:
        return None
    return float(mm)


def _material(text: str) -> str:
    folded = fold(text)
    if "inox" in folded:
        return "inox"
    if "galva" in folded or "galvanis" in folded:
        return "galvanise"
    if "laiton" in folded:
        return "laiton"
    if "pvc" in folded:
        return "pvc"
    if "bois" in folded:
        return "bois"
    return folded


def _canonicalize(rule: CategoryRule, attrs: dict) -> None:
    for key in (*rule.identity_keys, *_secondary_keys(rule)):
        if attrs.get(key) is not None:
            attrs[key] = _norm(attrs[key])


def _norm(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if number.is_integer():
            return int(number)
        return round(number, 2)
    text = fold(str(value)).strip()
    return text or None
