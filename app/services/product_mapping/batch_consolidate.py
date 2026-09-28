"""Batch Discovery V1.1 — consolidation READ-ONLY des candidats industrialisables.

Transforme les READY_EXISTING_MODEL + READY_GENERIC_FEATURES en familles métier
en classant la nature des clusters Discover et en dédoublonnant les SP.

Aucun Product / mapping / CategoryRule / écriture DB.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.services.product_mapping.batch_discover import (
    MODEL_BAGGED_MATERIAL,
    MODEL_BOARD_PANEL,
    MODEL_DIMENSIONAL_FASTENER,
    MODEL_INSULATION,
    MODEL_LINEAR_PROFILE,
    MODEL_PIPE,
    MODEL_TILE_OR_FLOORING,
    STATUS_READY_EXISTING,
    STATUS_READY_GENERIC,
    FamilyQualification,
    extract_raw_features,
    run_batch_discover,
)
from app.services.product_mapping.catalog_discover import (
    SupplierProductRow,
    content_tokens,
    load_supplier_products,
    product_id_snapshot_hash,
    resolve_supplier_ids,
)
# --- Nature du cluster -------------------------------------------------------

NATURE_PRODUCT = "PRODUCT_FAMILY"
NATURE_ATTRIBUTE = "ATTRIBUTE_CLUSTER"
NATURE_MIXED = "MIXED"
NATURE_UNCERTAIN = "UNCERTAIN"

NATURES = (NATURE_PRODUCT, NATURE_ATTRIBUTE, NATURE_MIXED, NATURE_UNCERTAIN)

# Contenance / Jaccard pour lier des clusters du même modèle.
CONTAINMENT_ATTR = 0.55  # A ⊂ B si |A∩B|/|A| ≥ seuil
JACCARD_LINK = 0.30

# Tokens d'attribut (tête, empreinte, finition…) — pas une famille produit.
_ATTR_TOKENS = frozenset(
    {
        "tete",
        "fraisee",
        "fraise",
        "plate",
        "bombee",
        "trompette",
        "hexagonale",
        "hexagonal",
        "cylindrique",
        "fraisée",
        "posidriv",
        "philips",
        "phillips",
        "torx",
        "cruciforme",
        "empreinte",
        "six",
        "pans",
        "zingué",
        "zingue",
        "zinc",
        "inox",
        "galvanise",
        "galvanisé",
        "laiton",
        "blanc",
        "noir",
        "jaune",
        "brut",
        "classe",
        "carbone",
        "trempe",
        "autoforeuse",
        "autoperforante",
        "autoperçante",
        "autopercante",
        "turbo",
        "agglo",  # qualificatif de vis, pas une famille seule
        "placo",
        "multi",
        "universelle",
        "universel",
    }
)

# Noms de produit (classe métier).
_PRODUCT_NOUNS = frozenset(
    {
        "vis",
        "cheville",
        "chevilles",
        "boulon",
        "boulons",
        "ecrou",
        "ecrous",
        "clou",
        "clous",
        "rivet",
        "goujon",
        "rondelle",
        "tirefond",
        "panneau",
        "panneaux",
        "plaque",
        "plaques",
        "osb",
        "contreplaque",
        "rail",
        "rails",
        "montant",
        "montants",
        "fourrure",
        "fourrures",
        "corniere",
        "cornieres",
        "profil",
        "profile",
        "tube",
        "tuyau",
        "raccord",
        "carrelage",
        "carreau",
        "parquet",
        "dalle",
        "ciment",
        "mortier",
        "colle",
        "enduit",
        "isolant",
        "laine",
        "peinture",
        "lasure",
        "vernis",
    }
)

# Attributs secondaires utiles pour l'audit fastener.
_SECONDARY_KEYS = ("packaging_qty", "profile")

_MODEL_PRIMITIVES: dict[str, dict[str, list[str]]] = {
    MODEL_DIMENSIONAL_FASTENER: {
        "available": ["diameter_mm", "length_mm / screw_length_mm", "extract_diameter_length_mm"],
        "missing": [],
    },
    MODEL_BOARD_PANEL: {
        "available": ["length_mm", "width_mm", "thickness_mm", "extract_dimensions_mm"],
        "missing": [],
    },
    MODEL_LINEAR_PROFILE: {
        "available": ["bar_length_mm", "profile", "extract_metal_profile", "NOMINAL_LENGTH_MAP"],
        "missing": [],
    },
    MODEL_PIPE: {
        "available": ["dn", "diameter_mm", "extract_dn"],
        "missing": ["material enum", "technical_type"],
    },
    MODEL_BAGGED_MATERIAL: {
        "available": ["weight_g", "extract_weight_g"],
        "missing": ["product_type taxonomy", "binder/usage"],
    },
    MODEL_TILE_OR_FLOORING: {
        "available": ["length_mm", "width_mm", "extract_length_width_mm"],
        "missing": ["surface_m2", "packaging_m2", "finish/usage"],
    },
    MODEL_INSULATION: {
        "available": ["thickness_mm", "extract_thickness_alone_mm"],
        "missing": ["R/lambda", "format (rouleau/panneau)"],
    },
}


@dataclass
class AuditedCandidate:
    family: str
    parent: str | None
    count: int
    candidate_model: str | None
    status: str
    nature: str
    nature_reasons: list[str]
    feature_highlights: dict[str, Any]
    structural_tokens: list[str]
    examples: list[dict[str, Any]]
    member_ids: tuple[int, ...]
    overlap_with: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "parent": self.parent,
            "count": self.count,
            "candidate_model": self.candidate_model,
            "status": self.status,
            "nature": self.nature,
            "nature_reasons": list(self.nature_reasons),
            "feature_highlights": dict(self.feature_highlights),
            "structural_tokens": list(self.structural_tokens),
            "examples": list(self.examples),
            "overlap_with": list(self.overlap_with),
            "member_count": len(self.member_ids),
        }


@dataclass
class ConsolidatedFamily:
    name: str
    candidate_model: str
    unique_sp: int
    source_clusters: list[str]
    attribute_clusters: list[str]
    nature: str
    complexity: str
    safe_quick_win: bool
    diameter_coverage: float | None = None
    length_coverage: float | None = None
    distinct_identities: int | None = None
    secondary_attrs: dict[str, Any] = field(default_factory=dict)
    false_positive_risks: list[str] = field(default_factory=list)
    existing_pmc_hint: str | None = None
    available_primitives: list[str] = field(default_factory=list)
    missing_primitives: list[str] = field(default_factory=list)
    business_decision_needed: str | None = None
    examples: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "candidate_model": self.candidate_model,
            "unique_sp": self.unique_sp,
            "source_clusters": list(self.source_clusters),
            "attribute_clusters": list(self.attribute_clusters),
            "nature": self.nature,
            "complexity": self.complexity,
            "safe_quick_win": self.safe_quick_win,
            "diameter_coverage": self.diameter_coverage,
            "length_coverage": self.length_coverage,
            "distinct_identities": self.distinct_identities,
            "secondary_attrs": dict(self.secondary_attrs),
            "false_positive_risks": list(self.false_positive_risks),
            "existing_pmc_hint": self.existing_pmc_hint,
            "available_primitives": list(self.available_primitives),
            "missing_primitives": list(self.missing_primitives),
            "business_decision_needed": self.business_decision_needed,
            "examples": list(self.examples),
            "reasons": list(self.reasons),
        }


@dataclass
class ConsolidationReport:
    supplier: str | None
    total_sp: int
    initial_candidates: int
    nature_counts: dict[str, int]
    audited: list[AuditedCandidate]
    consolidated_by_model: dict[str, list[ConsolidatedFamily]]
    consolidated_family_count: int
    unique_sp_total: int
    first_lot: list[ConsolidatedFamily]
    false_positives: list[str]
    duration_ms: int
    product_id_hash_before: str
    product_id_hash_after: str
    overlap_pairs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier,
            "total_sp": self.total_sp,
            "initial_candidates": self.initial_candidates,
            "nature_counts": dict(self.nature_counts),
            "audited": [a.to_dict() for a in self.audited],
            "consolidated_by_model": {
                m: [f.to_dict() for f in fams]
                for m, fams in sorted(self.consolidated_by_model.items())
            },
            "consolidated_family_count": self.consolidated_family_count,
            "unique_sp_total": self.unique_sp_total,
            "first_lot": [f.to_dict() for f in self.first_lot],
            "false_positives": list(self.false_positives),
            "overlap_pairs": list(self.overlap_pairs),
            "duration_ms": self.duration_ms,
            "product_id_hash_before": self.product_id_hash_before,
            "product_id_hash_after": self.product_id_hash_after,
        }


def _label_tokens(family: str) -> set[str]:
    return set(content_tokens(family.replace("_", " "), keep_numbers=False))


def classify_cluster_nature(
    family: str,
    *,
    parent: str | None,
    candidate_model: str | None,
) -> tuple[str, list[str]]:
    """PRODUCT_FAMILY / ATTRIBUTE_CLUSTER / MIXED / UNCERTAIN — critères lexicaux."""
    tokens = _label_tokens(family)
    if not tokens:
        return NATURE_UNCERTAIN, ["label sans token structurant"]

    product_hits = tokens & _PRODUCT_NOUNS
    attr_hits = tokens & _ATTR_TOKENS
    other = tokens - product_hits - attr_hits

    reasons = [
        f"tokens={sorted(tokens)}",
        f"product_nouns={sorted(product_hits)}",
        f"attr_tokens={sorted(attr_hits)}",
    ]
    if parent:
        reasons.append(f"parent_discover={parent}")

    # Attribut pur : aucun nom de produit, majorité attribut.
    if not product_hits and attr_hits and len(attr_hits) >= max(1, len(tokens) // 2):
        return NATURE_ATTRIBUTE, reasons + ["aucun nom de produit, attributs dominants"]

    # Famille produit : au moins un nom de produit, attributs non majoritaires.
    if product_hits and len(attr_hits) <= len(product_hits):
        return NATURE_PRODUCT, reasons + ["nom de produit présent, attributs non dominants"]

    # Mixte : produit + attributs forts.
    if product_hits and attr_hits and len(attr_hits) > len(product_hits):
        return NATURE_MIXED, reasons + ["nom de produit + attributs majoritaires"]

    # Qualificatif matériel seul (acier carbone…) sans produit.
    if not product_hits and other and not attr_hits:
        return NATURE_UNCERTAIN, reasons + ["tokens hors lexiques produit/attribut"]

    if product_hits:
        return NATURE_PRODUCT, reasons + ["nom de produit présent"]

    if attr_hits:
        return NATURE_ATTRIBUTE, reasons + ["attributs sans produit"]

    return NATURE_UNCERTAIN, reasons


def _feature_highlights(q: FamilyQualification) -> dict[str, Any]:
    keys = (
        "diameter_mm",
        "screw_length_mm",
        "diameter_x_length",
        "dimensions_lxwxt",
        "length_mm",
        "width_mm",
        "thickness_mm",
        "bar_length_mm",
        "profile",
        "dn",
        "volume_ml",
        "weight_g",
        "packaging_qty",
    )
    out: dict[str, Any] = {}
    for key in keys:
        feat = q.feature_coverage.get(key)
        if feat and feat.get("coverage", 0) > 0:
            out[key] = {
                "coverage": feat["coverage"],
                "distinct": feat["distinct"],
                "examples": feat.get("examples", [])[:3],
            }
    return out


def _overlap_stats(a: set[int], b: set[int]) -> dict[str, float | int]:
    inter = len(a & b)
    if not a or not b:
        return {"intersection": 0, "containment_a_in_b": 0.0, "containment_b_in_a": 0.0, "jaccard": 0.0}
    return {
        "intersection": inter,
        "containment_a_in_b": round(inter / len(a), 4),
        "containment_b_in_a": round(inter / len(b), 4),
        "jaccard": round(inter / len(a | b), 4),
    }


def build_overlap_pairs(
    candidates: Sequence[AuditedCandidate],
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(candidates):
        sa = set(a.member_ids)
        for b in candidates[i + 1 :]:
            if a.candidate_model != b.candidate_model:
                continue
            sb = set(b.member_ids)
            stats = _overlap_stats(sa, sb)
            if stats["intersection"] == 0:
                continue
            if (
                float(stats["jaccard"]) >= JACCARD_LINK
                or float(stats["containment_a_in_b"]) >= CONTAINMENT_ATTR
                or float(stats["containment_b_in_a"]) >= CONTAINMENT_ATTR
            ):
                pairs.append(
                    {
                        "a": a.family,
                        "b": b.family,
                        "model": a.candidate_model,
                        **stats,
                    }
                )
    pairs.sort(key=lambda p: (-int(p["intersection"]), p["a"], p["b"]))
    return pairs


def _connected_components(
    names: Sequence[str], pairs: Sequence[Mapping[str, Any]]
) -> list[set[str]]:
    parent: dict[str, str] = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for pair in pairs:
        if pair["a"] in parent and pair["b"] in parent:
            union(str(pair["a"]), str(pair["b"]))

    groups: dict[str, set[str]] = defaultdict(set)
    for n in names:
        groups[find(n)].add(n)
    return list(groups.values())


def _rows_by_id(rows: Sequence[SupplierProductRow]) -> dict[int, SupplierProductRow]:
    return {r.id: r for r in rows}


def _audit_fastener_detail(
    name: str,
    member_ids: Sequence[int],
    by_id: Mapping[int, SupplierProductRow],
    source_clusters: Sequence[str],
    attribute_clusters: Sequence[str],
) -> ConsolidatedFamily:
    rows = [by_id[i] for i in member_ids if i in by_id]
    n = len(rows) or 1
    diam_ok = 0
    len_ok = 0
    pairs: set[str] = set()
    pack_c: Counter[str] = Counter()
    fp_risks: list[str] = []
    examples: list[dict[str, Any]] = []
    for row in rows:
        feats = extract_raw_features(row.designation)
        if "diameter_mm" in feats:
            diam_ok += 1
        if "screw_length_mm" in feats or "diameter_x_length" in feats:
            len_ok += 1
        if "diameter_x_length" in feats:
            pairs.add(str(feats["diameter_x_length"]))
        if "packaging_qty" in feats:
            pack_c[str(feats["packaging_qty"])] += 1
        tokens = set(content_tokens(row.designation))
        if not tokens & {
            "vis",
            "cheville",
            "chevilles",
            "boulon",
            "boulons",
            "ecrou",
            "ecrous",
            "clou",
            "clous",
        }:
            fp_risks.append(
                f"id={row.id} sans nom de fixation: {(row.designation or '')[:80]}"
            )
        if len(examples) < 5:
            examples.append(
                {
                    "id": row.id,
                    "sku": row.supplier_reference,
                    "designation": (row.designation or "")[:160],
                }
            )

    # Dédupliquer risques
    fp_unique = list(dict.fromkeys(fp_risks))[:8]
    diam_cov = diam_ok / n
    length_cov = len_ok / n
    safe = (
        diam_cov >= 0.7
        and length_cov >= 0.7
        and len(pairs) >= 3
        and len(fp_unique) <= max(2, int(0.1 * n))
    )
    complexity = "XS" if safe else "S"
    pmc_hint = None
    low = name.lower()
    if "vis" in low and "bois" in low:
        pmc_hint = "proche VIS_AGGLO / future VIS_BOIS"
    elif "cheville" in low:
        pmc_hint = "future CHEVILLE_* (IdentityModel DIMENSIONAL_FASTENER)"
    elif "vis" in low:
        pmc_hint = "proche VIS_PLACO/VIS_AGGLO ou nouvelle règle type=…"

    return ConsolidatedFamily(
        name=name,
        candidate_model=MODEL_DIMENSIONAL_FASTENER,
        unique_sp=len(rows),
        source_clusters=list(source_clusters),
        attribute_clusters=list(attribute_clusters),
        nature=NATURE_PRODUCT,
        complexity=complexity,
        safe_quick_win=safe,
        diameter_coverage=round(diam_cov, 4),
        length_coverage=round(length_cov, 4),
        distinct_identities=len(pairs),
        secondary_attrs={
            "packaging_qty_top": [
                {"value": v, "count": c} for v, c in pack_c.most_common(5)
            ]
        },
        false_positive_risks=fp_unique,
        existing_pmc_hint=pmc_hint,
        available_primitives=_MODEL_PRIMITIVES[MODEL_DIMENSIONAL_FASTENER]["available"],
        missing_primitives=[],
        examples=examples,
        reasons=[
            f"Ø coverage={diam_cov:.0%}",
            f"L coverage={length_cov:.0%}",
            f"identités Ø×L distinctes={len(pairs)}",
            f"clusters sources={list(source_clusters)}",
            f"attributs rattachés={list(attribute_clusters)}",
        ],
    )


def _audit_panel_or_profile(
    name: str,
    model: str,
    member_ids: Sequence[int],
    by_id: Mapping[int, SupplierProductRow],
    source_clusters: Sequence[str],
    attribute_clusters: Sequence[str],
) -> ConsolidatedFamily:
    rows = [by_id[i] for i in member_ids if i in by_id]
    n = len(rows) or 1
    dim_ok = 0
    bar_ok = 0
    profile_ok = 0
    identities: set[str] = set()
    fp: list[str] = []
    examples = []
    for row in rows:
        feats = extract_raw_features(row.designation)
        if model == MODEL_BOARD_PANEL:
            if "dimensions_lxwxt" in feats:
                dim_ok += 1
                identities.add(str(feats["dimensions_lxwxt"]))
            elif all(k in feats for k in ("length_mm", "width_mm", "thickness_mm")):
                dim_ok += 1
                identities.add(
                    f"{feats['length_mm']}x{feats['width_mm']}x{feats['thickness_mm']}"
                )
            else:
                # Dimension parasite possible
                if "length_mm" in feats and "width_mm" not in feats:
                    fp.append(f"id={row.id} longueur seule (pas L×l×ép)")
        else:
            if "bar_length_mm" in feats:
                bar_ok += 1
            if "profile" in feats:
                profile_ok += 1
                identities.add(f"{feats.get('profile')}|{feats.get('bar_length_mm')}")
            elif "bar_length_mm" in feats:
                identities.add(str(feats["bar_length_mm"]))
        if len(examples) < 5:
            examples.append(
                {
                    "id": row.id,
                    "sku": row.supplier_reference,
                    "designation": (row.designation or "")[:160],
                }
            )

    if model == MODEL_BOARD_PANEL:
        cov = dim_ok / n
        safe = cov >= 0.6 and len(identities) >= 2 and len(fp) <= max(2, int(0.15 * n))
        return ConsolidatedFamily(
            name=name,
            candidate_model=model,
            unique_sp=len(rows),
            source_clusters=list(source_clusters),
            attribute_clusters=list(attribute_clusters),
            nature=NATURE_PRODUCT,
            complexity="XS" if safe else "S",
            safe_quick_win=safe,
            diameter_coverage=None,
            length_coverage=round(cov, 4),
            distinct_identities=len(identities),
            false_positive_risks=list(dict.fromkeys(fp))[:8],
            available_primitives=_MODEL_PRIMITIVES[model]["available"],
            missing_primitives=[],
            examples=examples,
            reasons=[
                f"L×l×ép coverage={cov:.0%}",
                f"identités distinctes={len(identities)}",
            ],
        )

    bar_cov = bar_ok / n
    prof_cov = profile_ok / n
    safe = bar_cov >= 0.5 and (prof_cov >= 0.3 or len(identities) >= 2)
    return ConsolidatedFamily(
        name=name,
        candidate_model=model,
        unique_sp=len(rows),
        source_clusters=list(source_clusters),
        attribute_clusters=list(attribute_clusters),
        nature=NATURE_PRODUCT,
        complexity="XS" if safe else "S",
        safe_quick_win=safe,
        length_coverage=round(bar_cov, 4),
        distinct_identities=len(identities),
        secondary_attrs={"profile_coverage": round(prof_cov, 4)},
        false_positive_risks=[],
        available_primitives=_MODEL_PRIMITIVES[model]["available"],
        missing_primitives=[],
        examples=examples,
        reasons=[
            f"bar_length coverage={bar_cov:.0%}",
            f"profile coverage={prof_cov:.0%}",
            f"identités distinctes={len(identities)}",
        ],
    )


def _audit_generic(
    name: str,
    model: str,
    member_ids: Sequence[int],
    by_id: Mapping[int, SupplierProductRow],
    source_clusters: Sequence[str],
    attribute_clusters: Sequence[str],
) -> ConsolidatedFamily:
    rows = [by_id[i] for i in member_ids if i in by_id]
    prim = _MODEL_PRIMITIVES.get(
        model, {"available": [], "missing": ["identité métier à définir"]}
    )
    decision = None
    if model == MODEL_TILE_OR_FLOORING:
        decision = "surface packagée / usage sol-mur à trancher"
    elif model == MODEL_BAGGED_MATERIAL:
        decision = "type produit (ciment/mortier/colle/enduit) comme clé d'identité ?"
    elif model == MODEL_INSULATION:
        decision = "R/lambda et format — vérifier faux positifs lexicaux"
    elif model == MODEL_PIPE:
        decision = "matériau + DN (+ longueur hors identité ?)"

    examples = [
        {
            "id": r.id,
            "sku": r.supplier_reference,
            "designation": (r.designation or "")[:160],
        }
        for r in rows[:5]
    ]
    return ConsolidatedFamily(
        name=name,
        candidate_model=model,
        unique_sp=len(rows),
        source_clusters=list(source_clusters),
        attribute_clusters=list(attribute_clusters),
        nature=NATURE_PRODUCT,
        complexity="S",
        safe_quick_win=False,
        available_primitives=list(prim["available"]),
        missing_primitives=list(prim["missing"]),
        business_decision_needed=decision,
        examples=examples,
        reasons=[f"modèle futur {model}", f"SP uniques={len(rows)}"],
    )


def consolidate_candidates(
    industrializable: Sequence[FamilyQualification],
    rows: Sequence[SupplierProductRow],
) -> tuple[
    list[AuditedCandidate],
    dict[str, list[ConsolidatedFamily]],
    list[dict[str, Any]],
    list[str],
]:
    """Cœur V1.1 : nature + overlaps + familles consolidées par modèle."""
    by_id = _rows_by_id(rows)
    audited: list[AuditedCandidate] = []
    for q in industrializable:
        nature, reasons = classify_cluster_nature(
            q.family, parent=q.parent, candidate_model=q.candidate_model
        )
        audited.append(
            AuditedCandidate(
                family=q.family,
                parent=q.parent,
                count=q.count,
                candidate_model=q.candidate_model,
                status=q.status,
                nature=nature,
                nature_reasons=reasons,
                feature_highlights=_feature_highlights(q),
                structural_tokens=sorted(_label_tokens(q.family)),
                examples=list(q.examples),
                member_ids=tuple(q.member_ids),
            )
        )

    pairs = build_overlap_pairs(audited)
    by_name = {a.family: a for a in audited}
    for a in audited:
        overlaps = []
        for p in pairs:
            other = None
            if p["a"] == a.family:
                other = p["b"]
            elif p["b"] == a.family:
                other = p["a"]
            if other is None:
                continue
            overlaps.append(
                {
                    "family": other,
                    "intersection": p["intersection"],
                    "jaccard": p["jaccard"],
                    "containment_self_in_other": (
                        p["containment_a_in_b"]
                        if p["a"] == a.family
                        else p["containment_b_in_a"]
                    ),
                }
            )
        a.overlap_with = sorted(overlaps, key=lambda o: -o["intersection"])

    consolidated: dict[str, list[ConsolidatedFamily]] = defaultdict(list)
    false_positives: list[str] = []

    for model in sorted(
        {a.candidate_model for a in audited if a.candidate_model}
    ):
        model_cands = [a for a in audited if a.candidate_model == model]
        names = [a.family for a in model_cands]
        model_pairs = [p for p in pairs if p["model"] == model]
        components = _connected_components(names, model_pairs)

        used: set[str] = set()
        for comp in sorted(components, key=lambda c: -sum(by_name[n].count for n in c)):
            members = [by_name[n] for n in comp]
            products = [m for m in members if m.nature == NATURE_PRODUCT]
            attrs = [m for m in members if m.nature == NATURE_ATTRIBUTE]
            mixed = [m for m in members if m.nature == NATURE_MIXED]
            uncertain = [m for m in members if m.nature == NATURE_UNCERTAIN]

            # Racines = PRODUCT_FAMILY ; à défaut MIXED/UNCERTAIN les plus gros.
            roots = products or mixed or uncertain or members
            # Si plusieurs roots qui se chevauchent fortement, garder le plus gros
            # et rattacher les autres en attributs si containment élevé.
            roots_sorted = sorted(roots, key=lambda m: -m.count)
            primary = roots_sorted[0]
            attached_attrs = list(attrs)
            for other in roots_sorted[1:]:
                stats = _overlap_stats(set(other.member_ids), set(primary.member_ids))
                if float(stats["containment_a_in_b"]) >= CONTAINMENT_ATTR:
                    attached_attrs.append(other)
                else:
                    # Famille distincte dans le même composant faible.
                    roots_extra = other
                    ids = set(roots_extra.member_ids)
                    # Retirer SP déjà dans primary pour éviter double comptage global
                    # — on compte unique par famille consolidée.
                    fam = _build_consolidated(
                        roots_extra.family,
                        model,
                        ids,
                        by_id,
                        [roots_extra.family],
                        [],
                    )
                    consolidated[model].append(fam)
                    used.add(roots_extra.family)

            ids = set(primary.member_ids)
            for att in attached_attrs:
                ids |= set(att.member_ids)
            fam = _build_consolidated(
                primary.family,
                model,
                ids,
                by_id,
                [primary.family] + [m.family for m in roots_sorted[1:] if m.family not in {a.family for a in attached_attrs}],
                [a.family for a in attached_attrs],
            )
            # Marquer faux positifs évidents
            if model == MODEL_BOARD_PANEL and "corniere" in primary.family.lower():
                false_positives.append(
                    f"{primary.family}: cornière classée BOARD_PANEL (profil linéaire probable)"
                )
                fam.false_positive_risks.append("cornière ≠ panneau")
                fam.safe_quick_win = False
            if model == MODEL_INSULATION and any(
                t in primary.family.lower() for t in ("fenetre", "gazon", "evier")
            ):
                false_positives.append(
                    f"{primary.family}: faux positif INSULATION lexical"
                )
                fam.safe_quick_win = False
            if model == MODEL_DIMENSIONAL_FASTENER and primary.nature == NATURE_ATTRIBUTE:
                false_positives.append(
                    f"{primary.family}: ATTRIBUTE_CLUSTER promu faute de PRODUCT_FAMILY"
                )
                fam.safe_quick_win = False
            consolidated[model].append(fam)
            used.add(primary.family)
            used.update(a.family for a in attached_attrs)

        # Singletons non liés
        for a in model_cands:
            if a.family in used:
                continue
            if a.nature == NATURE_ATTRIBUTE:
                false_positives.append(
                    f"{a.family}: ATTRIBUTE_CLUSTER orphelin (pas de famille produit parente)"
                )
                continue
            fam = _build_consolidated(
                a.family,
                model,
                set(a.member_ids),
                by_id,
                [a.family],
                [],
            )
            if a.nature == NATURE_UNCERTAIN:
                fam.safe_quick_win = False
                fam.complexity = "S"
                fam.false_positive_risks.append("nature UNCERTAIN")
            consolidated[model].append(fam)

        consolidated[model].sort(key=lambda f: (-f.unique_sp, f.name))

    return audited, dict(consolidated), pairs, list(dict.fromkeys(false_positives))


def _build_consolidated(
    name: str,
    model: str,
    member_ids: set[int],
    by_id: Mapping[int, SupplierProductRow],
    source_clusters: Sequence[str],
    attribute_clusters: Sequence[str],
) -> ConsolidatedFamily:
    if model == MODEL_DIMENSIONAL_FASTENER:
        return _audit_fastener_detail(
            name, sorted(member_ids), by_id, source_clusters, attribute_clusters
        )
    if model in (MODEL_BOARD_PANEL, MODEL_LINEAR_PROFILE):
        return _audit_panel_or_profile(
            name, model, sorted(member_ids), by_id, source_clusters, attribute_clusters
        )
    return _audit_generic(
        name, model, sorted(member_ids), by_id, source_clusters, attribute_clusters
    )


def propose_first_lot(
    consolidated_by_model: Mapping[str, Sequence[ConsolidatedFamily]],
) -> list[ConsolidatedFamily]:
    """Premier lot : maximiser IdentityModel existant, quick wins sûrs."""
    lot: list[ConsolidatedFamily] = []
    # Priorité : DIMENSIONAL_FASTENER safe XS, puis LINEAR_PROFILE, BOARD_PANEL.
    for model in (
        MODEL_DIMENSIONAL_FASTENER,
        MODEL_LINEAR_PROFILE,
        MODEL_BOARD_PANEL,
    ):
        for fam in consolidated_by_model.get(model, []):
            if fam.safe_quick_win and fam.nature == NATURE_PRODUCT:
                lot.append(fam)
    lot.sort(key=lambda f: (-f.unique_sp, f.candidate_model, f.name))
    return lot


def run_batch_consolidate(
    session: Session,
    *,
    supplier_name: str | None = None,
    min_size: int = 15,
    top: int = 50,
) -> ConsolidationReport:
    """Pipeline READ-ONLY V1 → V1.1."""
    started = time.perf_counter()
    supplier_ids = resolve_supplier_ids(session, supplier_name)
    rows = load_supplier_products(session, supplier_ids)
    hash_before = product_id_snapshot_hash(rows)

    discover = run_batch_discover(
        session,
        supplier_name=supplier_name,
        min_size=min_size,
        top=top,
    )
    industrializable = [
        f
        for f in discover.families
        if f.status in (STATUS_READY_EXISTING, STATUS_READY_GENERIC)
    ]
    audited, consolidated, pairs, fps = consolidate_candidates(
        industrializable, rows
    )
    nature_counts = {n: 0 for n in NATURES}
    for a in audited:
        nature_counts[a.nature] = nature_counts.get(a.nature, 0) + 1

    # SP uniques toutes familles consolidées (union)
    all_ids: set[int] = set()
    fam_count = 0
    for fams in consolidated.values():
        for fam in fams:
            fam_count += 1
            # reclaim ids from audited sources
            for src in fam.source_clusters + fam.attribute_clusters:
                for a in audited:
                    if a.family == src:
                        all_ids |= set(a.member_ids)

    first_lot = propose_first_lot(consolidated)

    after = load_supplier_products(session, supplier_ids)
    hash_after = product_id_snapshot_hash(after)
    if hash_after != hash_before:
        raise RuntimeError("catalog-batch-consolidate lecture seule : product_id modifié")

    duration = time.perf_counter() - started
    return ConsolidationReport(
        supplier=supplier_name,
        total_sp=len(rows),
        initial_candidates=len(industrializable),
        nature_counts=nature_counts,
        audited=sorted(audited, key=lambda a: (-a.count, a.family)),
        consolidated_by_model=consolidated,
        consolidated_family_count=fam_count,
        unique_sp_total=len(all_ids),
        first_lot=first_lot,
        false_positives=fps,
        duration_ms=int(round(duration * 1000)),
        product_id_hash_before=hash_before,
        product_id_hash_after=hash_after,
        overlap_pairs=pairs,
    )


def format_consolidation_text(report: ConsolidationReport) -> str:
    lines: list[str] = []
    add = lines.append
    add("=== CATALOG BATCH CONSOLIDATION V1.1 (lecture seule) ===")
    add(f"Fournisseur          : {report.supplier or '(tous)'}")
    add(f"TOTAL SP             : {report.total_sp}")
    add(f"Candidats initiaux   : {report.initial_candidates}")
    add(f"Familles consolidées : {report.consolidated_family_count}")
    add(f"SP uniques (union)   : {report.unique_sp_total}")
    add(f"Durée                : {report.duration_ms} ms")
    add("")
    add("--- Nature des 49 ---")
    for n in NATURES:
        add(f"{n:20s}: {report.nature_counts.get(n, 0)}")
    add("")
    add("--- Par modèle (familles consolidées) ---")
    for model, fams in sorted(report.consolidated_by_model.items()):
        sp = sum(f.unique_sp for f in fams)
        safe = sum(1 for f in fams if f.safe_quick_win)
        add(f"{model}: {len(fams)} familles / {sp} SP (safe_quick_wins={safe})")
        for f in fams[:12]:
            add(
                f"  - {f.name} n={f.unique_sp} [{f.complexity}] "
                f"safe={f.safe_quick_win} attrs={f.attribute_clusters}"
            )
    add("")
    add("--- Premier lot (IdentityModel existant, quick wins sûrs) ---")
    if not report.first_lot:
        add("  (aucun)")
    for i, f in enumerate(report.first_lot, 1):
        add(
            f"{i}. {f.name} [{f.candidate_model}] SP={f.unique_sp} "
            f"Ø={f.diameter_coverage} L={f.length_coverage} "
            f"ids={f.distinct_identities}"
        )
    add("")
    add("--- Faux positifs ---")
    for fp in report.false_positives[:25]:
        add(f"  ! {fp}")
    add("")
    add(
        "Hash OK"
        if report.product_id_hash_before == report.product_id_hash_after
        else "Hash ERREUR"
    )
    return "\n".join(lines)
