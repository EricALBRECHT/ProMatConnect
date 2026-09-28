"""Batch Discovery V1 — qualification READ-ONLY des familles Catalog Discover V2.

Réutilise le clustering V2 (pas de second moteur). Classe chaque famille
significative selon son potentiel d'industrialisation :

  KNOWN_CATEGORY | READY_EXISTING_MODEL | READY_GENERIC_FEATURES
  | NEEDS_IDENTITY_DECISION | POOR_DATA | NOISY_CLUSTER

Aucun Product / mapping / CategoryRule métier / apply / écriture DB.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.services.product_mapping.catalog_discover import (
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_RECLUSTER_THRESHOLD,
    ClusterReport,
    KnownCategoryStats,
    SupplierProductRow,
    assign_clusters_v2,
    classify_known_categories,
    content_tokens,
    head_and_qualifier,
    is_generic_cluster,
    load_supplier_products,
    product_id_snapshot_hash,
    resolve_supplier_ids,
)
from app.services.product_mapping.identity_models import (
    BOARD_PANEL,
    DIMENSIONAL_FASTENER,
    LINEAR_PROFILE,
)
from app.services.product_mapping.primitives import (
    extract_bar_length_mm,
    extract_diameter_length_mm,
    extract_dimensions_mm,
    extract_dn,
    extract_length_width_mm,
    extract_metal_profile,
    extract_piece_count,
    extract_thickness_alone_mm,
    extract_volume_ml,
    extract_weight_g,
)
from app.services.product_mapping.rules import registered_codes

# --- Statuts de travail -------------------------------------------------------

STATUS_KNOWN = "KNOWN_CATEGORY"
STATUS_READY_EXISTING = "READY_EXISTING_MODEL"
STATUS_READY_GENERIC = "READY_GENERIC_FEATURES"
STATUS_NEEDS_IDENTITY = "NEEDS_IDENTITY_DECISION"
STATUS_POOR_DATA = "POOR_DATA"
STATUS_NOISY = "NOISY_CLUSTER"

STATUSES = (
    STATUS_KNOWN,
    STATUS_READY_EXISTING,
    STATUS_READY_GENERIC,
    STATUS_NEEDS_IDENTITY,
    STATUS_POOR_DATA,
    STATUS_NOISY,
)

COMPLEXITY_XS = "XS"
COMPLEXITY_S = "S"
COMPLEXITY_M = "M"
COMPLEXITY_L = "L"

# Modèles IdentityModel existants (READY_EXISTING_MODEL possible).
MODEL_DIMENSIONAL_FASTENER = DIMENSIONAL_FASTENER.name
MODEL_LINEAR_PROFILE = LINEAR_PROFILE.name
MODEL_BOARD_PANEL = BOARD_PANEL.name

# Signatures futures — détection seule, pas d'IdentityModel créé.
MODEL_PIPE = "PIPE"
MODEL_LIQUID_FINISH = "LIQUID_FINISH"
MODEL_BAGGED_MATERIAL = "BAGGED_MATERIAL"
MODEL_TILE_OR_FLOORING = "TILE_OR_FLOORING"
MODEL_INSULATION = "INSULATION"

EXISTING_MODELS = frozenset(
    {MODEL_DIMENSIONAL_FASTENER, MODEL_LINEAR_PROFILE, MODEL_BOARD_PANEL}
)
FUTURE_MODELS = frozenset(
    {
        MODEL_PIPE,
        MODEL_LIQUID_FINISH,
        MODEL_BAGGED_MATERIAL,
        MODEL_TILE_OR_FLOORING,
        MODEL_INSULATION,
    }
)

DEFAULT_MIN_SIZE = 15
DEFAULT_TOP = 50
DEFAULT_EXAMPLE_COUNT = 8

# Seuils de couverture (déterministes, documentés).
COV_HIGH = 0.70
COV_MID = 0.50
COV_LOW = 0.35
LEX_HIGH = 0.50
LEX_MID = 0.35
TECH_POOR = 0.15

# Lexiques (tokens repliés) — heuristiques transparentes, pas du ML.
_FASTENER_LEX = frozenset(
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
        "rivets",
        "goujon",
        "goujons",
        "tirefond",
        "tirefonds",
        "fixation",
        "fixations",
        "rondelle",
        "rondelles",
        "agrafe",
        "agrafes",
    }
)
_PROFILE_LEX = frozenset(
    {
        "rail",
        "rails",
        "montant",
        "montants",
        "fourrure",
        "fourrures",
        "profil",
        "profils",
        "profile",
        "corniere",
        "cornieres",
        "ossature",
    }
)
_PANEL_LEX = frozenset(
    {
        "plaque",
        "plaques",
        "panneau",
        "panneaux",
        "osb",
        "ba13",
        "ba10",
        "ba18",
        "contreplaque",
        "melamine",
        "agglomere",
        "lamelle",
    }
)
_PIPE_LEX = frozenset(
    {
        "tube",
        "tubes",
        "tuyau",
        "tuyaux",
        "raccord",
        "raccords",
        "pvc",
        "per",
        "cuivre",
        "multicouche",
        "evacuation",
        "canalisation",
    }
)
_PAINT_LEX = frozenset(
    {
        "peinture",
        "peintures",
        "lasure",
        "lasures",
        "vernis",
        "laque",
        "laques",
        "acrylique",
        "glycero",
        "mat",
        "mate",
        "satine",
        "satinee",
        "brillant",
        "souscouche",
        "sous",
        "couche",
        "teinte",
        "coloris",
        "base",
        "testeur",
        "testeurs",
    }
)
_LIQUID_AMBIGUITY = frozenset(
    {"teinte", "coloris", "base", "testeur", "testeurs", "ral", "nuancier"}
)
_BAGGED_LEX = frozenset(
    {
        "ciment",
        "mortier",
        "colle",
        "colles",
        "enduit",
        "enduits",
        "beton",
        "platre",
        "chape",
        "ragreage",
        "joint",
        "joints",
        "sac",
        "sacs",
    }
)
_TILE_LEX = frozenset(
    {
        "carrelage",
        "carreau",
        "carreaux",
        "faience",
        "parquet",
        "lame",
        "lames",
        "lamine",
        "dalle",
        "dalles",
        "vinyle",
        "pvc",
        "sol",
    }
)
_INSULATION_LEX = frozenset(
    {
        "isolant",
        "isolation",
        "laine",
        "polystyrene",
        "xps",
        "eps",
        "liege",
        "multicouche",
        "panneau",
        "rouleau",
        "lambda",
    }
)


# --- Structures --------------------------------------------------------------


@dataclass(frozen=True)
class FeatureStatDetail:
    coverage: float
    distinct: int
    examples: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage": round(self.coverage, 4),
            "distinct": self.distinct,
            "examples": list(self.examples),
        }


@dataclass
class FamilyQualification:
    family: str
    parent: str | None
    count: int
    status: str
    candidate_model: str | None
    reasons: list[str]
    feature_coverage: dict[str, dict[str, Any]]
    mapped_count: int
    examples: list[dict[str, Any]]
    complexity: str
    lexical_coverage: dict[str, float] = field(default_factory=dict)
    unmapped_count: int = 0
    # IDs membres pour consolidation V1.1 (chevauchements) — hors JSON verbeux par défaut.
    member_ids: tuple[int, ...] = ()

    def to_dict(self, *, include_member_ids: bool = False) -> dict[str, Any]:
        payload = {
            "family": self.family,
            "parent": self.parent,
            "count": self.count,
            "status": self.status,
            "candidate_model": self.candidate_model,
            "reasons": list(self.reasons),
            "feature_coverage": dict(self.feature_coverage),
            "mapped_count": self.mapped_count,
            "unmapped_count": self.unmapped_count,
            "examples": list(self.examples),
            "complexity": self.complexity,
            "lexical_coverage": {
                k: round(v, 4) for k, v in sorted(self.lexical_coverage.items())
            },
        }
        if include_member_ids:
            payload["member_ids"] = list(self.member_ids)
        return payload


@dataclass
class BatchDiscoverReport:
    supplier: str | None
    min_size: int
    total_sp: int
    families_analysed: int
    status_counts: dict[str, int]
    families: list[FamilyQualification]
    quick_wins: list[FamilyQualification]
    high_impact: list[FamilyQualification]
    duration_ms: int
    sp_per_sec: float
    product_id_hash_before: str
    product_id_hash_after: str
    db_queries_estimate: int = 3  # resolve suppliers + load SP (+ reload garde-fou)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier,
            "min_size": self.min_size,
            "total_sp": self.total_sp,
            "families_analysed": self.families_analysed,
            "status_counts": dict(self.status_counts),
            "families": [f.to_dict() for f in self.families],
            "quick_wins": [f.to_dict() for f in self.quick_wins],
            "high_impact": [f.to_dict() for f in self.high_impact],
            "duration_ms": self.duration_ms,
            "sp_per_sec": self.sp_per_sec,
            "product_id_hash_before": self.product_id_hash_before,
            "product_id_hash_after": self.product_id_hash_after,
            "db_queries_estimate": self.db_queries_estimate,
            "industrializable": self.industrializable_summary(),
        }

    def industrializable_summary(self) -> dict[str, Any]:
        """Réponse à « combien sans audit métier complet ? »."""
        ready_existing = [f for f in self.families if f.status == STATUS_READY_EXISTING]
        ready_generic = [f for f in self.families if f.status == STATUS_READY_GENERIC]
        sp_existing = sum(f.count for f in ready_existing)
        sp_generic = sum(f.count for f in ready_generic)
        total = self.total_sp or 1
        return {
            "READY_EXISTING_MODEL": {
                "families": len(ready_existing),
                "supplier_products": sp_existing,
                "pct_catalog": round(100.0 * sp_existing / total, 2),
            },
            "READY_GENERIC_FEATURES": {
                "families": len(ready_generic),
                "supplier_products": sp_generic,
                "pct_catalog": round(100.0 * sp_generic / total, 2),
            },
            "combined": {
                "families": len(ready_existing) + len(ready_generic),
                "supplier_products": sp_existing + sp_generic,
                "pct_catalog": round(100.0 * (sp_existing + sp_generic) / total, 2),
            },
        }


# --- Extraction de signature -------------------------------------------------


def extract_raw_features(designation: str) -> dict[str, Any]:
    """Primitives numériques / profil — une passe, clés stables."""
    out: dict[str, Any] = {}
    length, width, thickness = extract_dimensions_mm(designation)
    if length is not None:
        out["length_mm"] = length
    if width is not None:
        out["width_mm"] = width
    if thickness is not None:
        out["thickness_mm"] = thickness
    if length is not None and width is not None and thickness is not None:
        out["dimensions_lxwxt"] = f"{length}x{width}x{thickness}"

    if "length_mm" not in out or "width_mm" not in out:
        lw = extract_length_width_mm(designation)
        if lw[0] is not None and "length_mm" not in out:
            out["length_mm"] = lw[0]
        if lw[1] is not None and "width_mm" not in out:
            out["width_mm"] = lw[1]

    if "thickness_mm" not in out:
        alone = extract_thickness_alone_mm(designation)
        if alone is not None:
            out["thickness_mm"] = alone

    diameter, screw_len = extract_diameter_length_mm(designation)
    if diameter is not None:
        out["diameter_mm"] = float(diameter)
    if screw_len is not None:
        out["screw_length_mm"] = screw_len
    if diameter is not None and screw_len is not None:
        out["diameter_x_length"] = f"{diameter}x{screw_len}"

    bar = extract_bar_length_mm(designation)
    if bar is not None:
        out["bar_length_mm"] = bar

    profile = extract_metal_profile(designation)
    if profile is not None:
        out["profile"] = profile

    volume = extract_volume_ml(designation)
    if volume is not None:
        out["volume_ml"] = volume
    weight = extract_weight_g(designation)
    if weight is not None:
        out["weight_g"] = weight
    dn = extract_dn(designation)
    if dn is not None:
        out["dn"] = dn
    pieces = extract_piece_count(designation)
    if pieces is not None:
        out["packaging_qty"] = pieces
    return out


def _token_set(designation: str) -> set[str]:
    return set(content_tokens(designation, keep_numbers=False))


def _lex_hit(tokens: set[str], lexicon: frozenset[str]) -> bool:
    return bool(tokens & lexicon)


def build_family_signature(
    rows: Sequence[SupplierProductRow],
    *,
    example_limit: int = DEFAULT_EXAMPLE_COUNT,
) -> dict[str, Any]:
    """Signature agrégée d'une famille (couvertures + lexique + exemples)."""
    n = len(rows)
    if n == 0:
        return {
            "count": 0,
            "mapped": 0,
            "features": {},
            "lexical": {},
            "examples": [],
            "technical_coverage": 0.0,
        }

    value_counters: dict[str, Counter[str]] = {}
    present: Counter[str] = Counter()
    lex_hits = {
        "fastener": 0,
        "profile": 0,
        "panel": 0,
        "pipe": 0,
        "paint": 0,
        "liquid_ambiguity": 0,
        "bagged": 0,
        "tile": 0,
        "insulation": 0,
    }
    technical = 0
    mapped = 0
    examples: list[dict[str, Any]] = []

    for row in sorted(rows, key=lambda r: r.id):
        if row.mapped:
            mapped += 1
        feats = extract_raw_features(row.designation)
        if feats:
            technical += 1
        for key, value in feats.items():
            present[key] += 1
            value_counters.setdefault(key, Counter())[str(value)] += 1

        tokens = _token_set(row.designation)
        if _lex_hit(tokens, _FASTENER_LEX):
            lex_hits["fastener"] += 1
        if _lex_hit(tokens, _PROFILE_LEX):
            lex_hits["profile"] += 1
        if _lex_hit(tokens, _PANEL_LEX):
            lex_hits["panel"] += 1
        if _lex_hit(tokens, _PIPE_LEX):
            lex_hits["pipe"] += 1
        if _lex_hit(tokens, _PAINT_LEX):
            lex_hits["paint"] += 1
        if _lex_hit(tokens, _LIQUID_AMBIGUITY):
            lex_hits["liquid_ambiguity"] += 1
        if _lex_hit(tokens, _BAGGED_LEX):
            lex_hits["bagged"] += 1
        if _lex_hit(tokens, _TILE_LEX):
            lex_hits["tile"] += 1
        if _lex_hit(tokens, _INSULATION_LEX):
            lex_hits["insulation"] += 1

        if len(examples) < example_limit:
            examples.append(
                {
                    "id": row.id,
                    "sku": row.supplier_reference,
                    "designation": (row.designation or "")[:180],
                }
            )

    features: dict[str, FeatureStatDetail] = {}
    for key, count in present.items():
        top = sorted(value_counters[key].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        features[key] = FeatureStatDetail(
            coverage=count / n,
            distinct=len(value_counters[key]),
            examples=tuple(v for v, _ in top),
        )

    lexical = {name: hits / n for name, hits in lex_hits.items()}
    return {
        "count": n,
        "mapped": mapped,
        "features": features,
        "lexical": lexical,
        "examples": examples,
        "technical_coverage": technical / n,
    }


def _cov(sig: Mapping[str, Any], key: str) -> float:
    feat = sig["features"].get(key)
    return float(feat.coverage) if feat else 0.0


def _distinct(sig: Mapping[str, Any], key: str) -> int:
    feat = sig["features"].get(key)
    return int(feat.distinct) if feat else 0


def _lex(sig: Mapping[str, Any], key: str) -> float:
    return float(sig["lexical"].get(key, 0.0))


# --- Qualification déterministe ---------------------------------------------


def _candidate_dimensional_fastener(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    d = _cov(sig, "diameter_mm")
    L = _cov(sig, "screw_length_mm")
    pair = _cov(sig, "diameter_x_length")
    lex = _lex(sig, "fastener")
    ok = (d >= COV_HIGH and L >= COV_HIGH or pair >= COV_HIGH) and lex >= LEX_MID
    reasons = [
        f"diameter_mm coverage={d:.0%}",
        f"screw_length_mm coverage={L:.0%}",
        f"diameter_x_length coverage={pair:.0%}",
        f"distinct couples={_distinct(sig, 'diameter_x_length')}",
        f"fastener tokens={lex:.0%}",
    ]
    return ok, reasons


def _candidate_linear_profile(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    bar = _cov(sig, "bar_length_mm")
    profile = _cov(sig, "profile")
    lex = _lex(sig, "profile")
    ok = bar >= COV_MID and (profile >= COV_LOW or lex >= LEX_MID)
    reasons = [
        f"bar_length_mm coverage={bar:.0%}",
        f"profile coverage={profile:.0%}",
        f"profile tokens={lex:.0%}",
        f"distinct lengths={_distinct(sig, 'bar_length_mm')}",
    ]
    return ok, reasons


def _candidate_board_panel(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    trip = _cov(sig, "dimensions_lxwxt")
    L = _cov(sig, "length_mm")
    W = _cov(sig, "width_mm")
    T = _cov(sig, "thickness_mm")
    lex = _lex(sig, "panel")
    ok = (trip >= COV_MID or (L >= COV_MID and W >= COV_MID and T >= COV_LOW)) and (
        lex >= LEX_MID or trip >= COV_HIGH
    )
    reasons = [
        f"dimensions_lxwxt coverage={trip:.0%}",
        f"length/width/thickness={L:.0%}/{W:.0%}/{T:.0%}",
        f"panel tokens={lex:.0%}",
        f"distinct panels={_distinct(sig, 'dimensions_lxwxt')}",
    ]
    return ok, reasons


def _candidate_pipe(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    dn = _cov(sig, "dn")
    d = _cov(sig, "diameter_mm")
    lex = _lex(sig, "pipe")
    ok = lex >= LEX_MID and (dn >= COV_LOW or d >= COV_MID)
    reasons = [
        f"dn coverage={dn:.0%}",
        f"diameter_mm coverage={d:.0%}",
        f"pipe tokens={lex:.0%}",
    ]
    return ok, reasons


def _candidate_liquid_finish(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    vol = _cov(sig, "volume_ml")
    paint = _lex(sig, "paint")
    amb = _lex(sig, "liquid_ambiguity")
    ok = vol >= COV_MID and paint >= LEX_MID
    reasons = [
        f"volume_ml coverage={vol:.0%}",
        f"paint/finish tokens={paint:.0%}",
        f"teinte/base/testeur tokens={amb:.0%}",
        f"distinct volumes={_distinct(sig, 'volume_ml')}",
    ]
    return ok, reasons


def _candidate_bagged(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    w = _cov(sig, "weight_g")
    lex = _lex(sig, "bagged")
    ok = w >= COV_MID and lex >= LEX_MID
    reasons = [
        f"weight_g coverage={w:.0%}",
        f"bagged tokens={lex:.0%}",
        f"distinct weights={_distinct(sig, 'weight_g')}",
    ]
    return ok, reasons


def _candidate_tile(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    L = _cov(sig, "length_mm")
    W = _cov(sig, "width_mm")
    lex = _lex(sig, "tile")
    ok = lex >= LEX_MID and L >= COV_LOW and W >= COV_LOW
    reasons = [
        f"length/width={L:.0%}/{W:.0%}",
        f"tile tokens={lex:.0%}",
    ]
    return ok, reasons


def _candidate_insulation(sig: Mapping[str, Any]) -> tuple[bool, list[str]]:
    t = _cov(sig, "thickness_mm")
    lex = _lex(sig, "insulation")
    ok = t >= COV_MID and lex >= LEX_MID
    reasons = [
        f"thickness_mm coverage={t:.0%}",
        f"insulation tokens={lex:.0%}",
    ]
    return ok, reasons


def detect_candidate_model(sig: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    """Retourne (candidate_model, reasons) — premier match par priorité métier."""
    checks = (
        (MODEL_DIMENSIONAL_FASTENER, _candidate_dimensional_fastener),
        (MODEL_BOARD_PANEL, _candidate_board_panel),
        (MODEL_LINEAR_PROFILE, _candidate_linear_profile),
        (MODEL_PIPE, _candidate_pipe),
        (MODEL_LIQUID_FINISH, _candidate_liquid_finish),
        (MODEL_BAGGED_MATERIAL, _candidate_bagged),
        (MODEL_TILE_OR_FLOORING, _candidate_tile),
        (MODEL_INSULATION, _candidate_insulation),
    )
    for name, fn in checks:
        ok, reasons = fn(sig)
        if ok:
            return name, reasons
    return None, ["no identity pattern matched thresholds"]


def classify_family(
    *,
    family: str,
    parent: str | None,
    sig: Mapping[str, Any],
    generic_cluster: bool = False,
    known_category: str | None = None,
    member_ids: Sequence[int] = (),
) -> FamilyQualification:
    """Classement déterministe d'une famille (pure, testable sans DB)."""
    count = int(sig["count"])
    mapped = int(sig["mapped"])
    unmapped = count - mapped
    feature_coverage = {
        k: v.to_dict() for k, v in sorted(sig["features"].items(), key=lambda kv: kv[0])
    }
    lexical = dict(sig["lexical"])
    examples = list(sig["examples"])
    ids = tuple(sorted({int(i) for i in member_ids}))

    if known_category:
        return FamilyQualification(
            family=known_category,
            parent=None,
            count=count,
            status=STATUS_KNOWN,
            candidate_model=None,
            reasons=[f"CategoryRule enregistrée: {known_category}"],
            feature_coverage=feature_coverage,
            mapped_count=mapped,
            unmapped_count=unmapped,
            examples=examples,
            complexity=COMPLEXITY_XS,
            lexical_coverage=lexical,
            member_ids=ids,
        )

    if generic_cluster:
        return FamilyQualification(
            family=family,
            parent=parent,
            count=count,
            status=STATUS_NOISY,
            candidate_model=None,
            reasons=[
                "Discover V2 generic_cluster=True",
                f"technical_coverage={sig['technical_coverage']:.0%}",
            ],
            feature_coverage=feature_coverage,
            mapped_count=mapped,
            unmapped_count=unmapped,
            examples=examples,
            complexity=COMPLEXITY_L,
            lexical_coverage=lexical,
            member_ids=ids,
        )

    if count > 0 and sig["technical_coverage"] < TECH_POOR and max(lexical.values(), default=0) < LEX_MID:
        return FamilyQualification(
            family=family,
            parent=parent,
            count=count,
            status=STATUS_POOR_DATA,
            candidate_model=None,
            reasons=[
                f"technical_coverage={sig['technical_coverage']:.0%} < {TECH_POOR:.0%}",
                "lexical signal faible",
            ],
            feature_coverage=feature_coverage,
            mapped_count=mapped,
            unmapped_count=unmapped,
            examples=examples,
            complexity=COMPLEXITY_L,
            lexical_coverage=lexical,
            member_ids=ids,
        )

    model, model_reasons = detect_candidate_model(sig)

    # Peinture / finition liquide : features riches mais identité métier ouverte.
    if model == MODEL_LIQUID_FINISH:
        return FamilyQualification(
            family=family,
            parent=parent,
            count=count,
            status=STATUS_NEEDS_IDENTITY,
            candidate_model=model,
            reasons=model_reasons
            + [
                "politique teinte/base/testeur non arrêtée",
                "ne pas traiter comme READY_EXISTING_MODEL",
            ],
            feature_coverage=feature_coverage,
            mapped_count=mapped,
            unmapped_count=unmapped,
            examples=examples,
            complexity=COMPLEXITY_M,
            lexical_coverage=lexical,
            member_ids=ids,
        )

    if model in EXISTING_MODELS:
        return FamilyQualification(
            family=family,
            parent=parent,
            count=count,
            status=STATUS_READY_EXISTING,
            candidate_model=model,
            reasons=model_reasons
            + [f"IdentityModel existant réutilisable: {model}"],
            feature_coverage=feature_coverage,
            mapped_count=mapped,
            unmapped_count=unmapped,
            examples=examples,
            complexity=COMPLEXITY_XS,
            lexical_coverage=lexical,
            member_ids=ids,
        )

    if model in FUTURE_MODELS:
        return FamilyQualification(
            family=family,
            parent=parent,
            count=count,
            status=STATUS_READY_GENERIC,
            candidate_model=model,
            reasons=model_reasons
            + [
                f"signature {model} détectée (IdentityModel non implémenté)",
                "primitives génériques déjà disponibles",
            ],
            feature_coverage=feature_coverage,
            mapped_count=mapped,
            unmapped_count=unmapped,
            examples=examples,
            complexity=COMPLEXITY_S,
            lexical_coverage=lexical,
            member_ids=ids,
        )

    # Features techniques présentes sans pattern clair.
    if sig["technical_coverage"] >= COV_MID:
        return FamilyQualification(
            family=family,
            parent=parent,
            count=count,
            status=STATUS_NEEDS_IDENTITY,
            candidate_model=None,
            reasons=[
                f"technical_coverage={sig['technical_coverage']:.0%}",
                "pas de pattern d'identité reconnu",
            ]
            + model_reasons,
            feature_coverage=feature_coverage,
            mapped_count=mapped,
            unmapped_count=unmapped,
            examples=examples,
            complexity=COMPLEXITY_M,
            lexical_coverage=lexical,
            member_ids=ids,
        )

    return FamilyQualification(
        family=family,
        parent=parent,
        count=count,
        status=STATUS_POOR_DATA,
        candidate_model=None,
        reasons=model_reasons
        + [f"technical_coverage={sig['technical_coverage']:.0%}"],
        feature_coverage=feature_coverage,
        mapped_count=mapped,
        unmapped_count=unmapped,
        examples=examples,
        complexity=COMPLEXITY_L,
        lexical_coverage=lexical,
        member_ids=ids,
    )


def _complexity_rank(c: str) -> int:
    return {COMPLEXITY_XS: 0, COMPLEXITY_S: 1, COMPLEXITY_M: 2, COMPLEXITY_L: 3}.get(c, 9)


def select_quick_wins(
    families: Sequence[FamilyQualification], *, top: int
) -> list[FamilyQualification]:
    pool = [
        f
        for f in families
        if f.status in (STATUS_READY_EXISTING, STATUS_READY_GENERIC)
        and f.complexity in (COMPLEXITY_XS, COMPLEXITY_S)
    ]
    pool.sort(
        key=lambda f: (
            _complexity_rank(f.complexity),
            -f.unmapped_count,
            -f.count,
            f.family,
        )
    )
    return pool[:top]


def select_high_impact(
    families: Sequence[FamilyQualification], *, top: int
) -> list[FamilyQualification]:
    pool = [
        f
        for f in families
        if f.status
        in (
            STATUS_READY_EXISTING,
            STATUS_READY_GENERIC,
            STATUS_NEEDS_IDENTITY,
        )
    ]
    pool.sort(key=lambda f: (-f.unmapped_count, -f.count, f.family))
    return pool[:top]


# --- Orchestration (une passe catalogue) ------------------------------------


def run_batch_discover(
    session: Session,
    *,
    supplier_name: str | None = None,
    min_size: int = DEFAULT_MIN_SIZE,
    top: int = DEFAULT_TOP,
    example_limit: int = DEFAULT_EXAMPLE_COUNT,
    min_cluster_size: int | None = None,
    recluster_threshold: int = DEFAULT_RECLUSTER_THRESHOLD,
) -> BatchDiscoverReport:
    """Analyse READ-ONLY : Discover V2 + qualification des familles >= min_size."""
    if min_size < 1:
        raise ValueError("min_size >= 1")
    cluster_min = min_cluster_size if min_cluster_size is not None else min(
        DEFAULT_MIN_CLUSTER_SIZE, min_size
    )
    started = time.perf_counter()
    supplier_ids = resolve_supplier_ids(session, supplier_name)
    rows = load_supplier_products(session, supplier_ids)
    hash_before = product_id_snapshot_hash(rows)

    known_stats, assigned = classify_known_categories(session, rows)
    remainder = [row for row in rows if row.id not in assigned]

    assignment = assign_clusters_v2(
        remainder,
        min_cluster_size=cluster_min,
        recluster_threshold=recluster_threshold,
    )

    families: list[FamilyQualification] = []

    # 1) Catégories déjà industrialisées
    for code in sorted(registered_codes()):
        stat: KnownCategoryStats = known_stats[code]
        # Reconstituer un sous-ensemble pour signature (SP assignés à cette règle).
        known_rows = [r for r in rows if assigned.get(r.id) == code]
        sig = build_family_signature(known_rows, example_limit=example_limit)
        families.append(
            classify_family(
                family=code,
                parent=None,
                sig=sig,
                known_category=code,
                member_ids=[r.id for r in known_rows],
            )
        )

    # 2) Clusters Discover V2 (parents + enfants significatifs)
    for group in assignment.groups:
        parent_label = group.seed.upper()
        parent_sig = build_family_signature(group.members, example_limit=example_limit)
        head_tok, qual_tok = head_and_qualifier(
            group.seed, group.seed_kind, assignment.idf_of_token
        )
        parent_report = ClusterReport(
            label=parent_label,
            seed_token=group.seed,
            sp_count=len(group.members),
            mapped=parent_sig["mapped"],
            unmapped=len(group.members) - parent_sig["mapped"],
            label_cohesion=0.0,
            technical_feature_coverage=parent_sig["technical_coverage"],
            seed_kind=group.seed_kind,
            head=head_tok,
            qualifier=qual_tok,
            document_frequency=assignment.seed_df(group),
            discrimination=assignment.seed_idf(group),
            co_token_count=0,
            distinct_local_bigrams=group.local_bigram_seeds,
            top_child_share=(
                max((len(m) for m in group.children.values()), default=0)
                / len(group.members)
                if group.members and group.children
                else 0.0
            ),
        )
        parent_generic = is_generic_cluster(
            parent_report,
            low_idf_threshold=assignment.low_idf_threshold,
            split=group.split,
        )

        child_quals: list[FamilyQualification] = []
        for child_seed, child_members in sorted(
            group.children.items(), key=lambda kv: (-len(kv[1]), kv[0])
        ):
            if len(child_members) < min_size:
                continue
            child_sig = build_family_signature(
                child_members, example_limit=example_limit
            )
            child_quals.append(
                classify_family(
                    family=child_seed.upper(),
                    parent=parent_label,
                    sig=child_sig,
                    generic_cluster=False,
                    member_ids=[r.id for r in child_members],
                )
            )

        if parent_generic:
            if len(group.members) >= min_size:
                families.append(
                    classify_family(
                        family=parent_label,
                        parent=None,
                        sig=parent_sig,
                        generic_cluster=True,
                        member_ids=[r.id for r in group.members],
                    )
                )
            families.extend(child_quals)
        else:
            if len(group.members) >= min_size:
                families.append(
                    classify_family(
                        family=parent_label,
                        parent=None,
                        sig=parent_sig,
                        generic_cluster=False,
                        member_ids=[r.id for r in group.members],
                    )
                )
            families.extend(child_quals)

    # Dédupliquer par (family, parent) en gardant le plus gros count
    dedup: dict[tuple[str, str | None], FamilyQualification] = {}
    for fam in families:
        key = (fam.family, fam.parent)
        prev = dedup.get(key)
        if prev is None or fam.count > prev.count:
            dedup[key] = fam
    families = sorted(
        dedup.values(),
        key=lambda f: (-f.unmapped_count, -f.count, f.family),
    )

    status_counts = {s: 0 for s in STATUSES}
    for fam in families:
        status_counts[fam.status] = status_counts.get(fam.status, 0) + 1

    quick = select_quick_wins(families, top=top)
    impact = select_high_impact(families, top=top)

    duration = time.perf_counter() - started
    after = load_supplier_products(session, supplier_ids)
    hash_after = product_id_snapshot_hash(after)
    if hash_after != hash_before:
        raise RuntimeError(
            "catalog-batch-discover est en lecture seule : product_id modifié"
        )

    return BatchDiscoverReport(
        supplier=supplier_name,
        min_size=min_size,
        total_sp=len(rows),
        families_analysed=len(families),
        status_counts=status_counts,
        families=families,
        quick_wins=quick,
        high_impact=impact,
        duration_ms=int(round(duration * 1000)),
        sp_per_sec=round(len(rows) / duration, 1) if duration > 0 else 0.0,
        product_id_hash_before=hash_before,
        product_id_hash_after=hash_after,
        db_queries_estimate=3,
    )


def format_text_report(report: BatchDiscoverReport, *, top: int | None = None) -> str:
    top_n = top if top is not None else len(report.quick_wins)
    lines: list[str] = []
    add = lines.append
    add("=== CATALOG BATCH DISCOVERY V1 (lecture seule) ===")
    add(f"Fournisseur     : {report.supplier or '(tous)'}")
    add(f"min-size        : {report.min_size}")
    add(f"Families analysed: {report.families_analysed}")
    add(f"TOTAL SP        : {report.total_sp}")
    add(f"Durée           : {report.duration_ms} ms ({report.sp_per_sec} SP/s)")
    add(f"DB queries ~    : {report.db_queries_estimate}")
    add("")
    add("--- Statuts ---")
    for status in STATUSES:
        add(f"{status:28s}: {report.status_counts.get(status, 0)}")
    add("")
    indust = report.industrializable_summary()
    add("--- Industrialisables sans audit métier complet ---")
    for key in ("READY_EXISTING_MODEL", "READY_GENERIC_FEATURES", "combined"):
        block = indust[key]
        add(
            f"{key:28s}: {block['families']} familles / "
            f"{block['supplier_products']} SP ({block['pct_catalog']}%)"
        )
    add("")
    add(f"--- QUICK WINS (top {top_n}) ---")
    for i, fam in enumerate(report.quick_wins[:top_n], 1):
        add(
            f"{i:>2}. [{fam.complexity}] {fam.family}"
            f"{' < '+fam.parent if fam.parent else ''} "
            f"n={fam.count} unmapped={fam.unmapped_count} "
            f"model={fam.candidate_model} status={fam.status}"
        )
        add(f"    reasons: {'; '.join(fam.reasons[:3])}")
    add("")
    add(f"--- HIGH IMPACT (top {top_n}) ---")
    for i, fam in enumerate(report.high_impact[:top_n], 1):
        add(
            f"{i:>2}. [{fam.complexity}] {fam.family}"
            f"{' < '+fam.parent if fam.parent else ''} "
            f"n={fam.count} unmapped={fam.unmapped_count} "
            f"model={fam.candidate_model} status={fam.status}"
        )
    add("")
    add("--- Hash product_id (avant = après) ---")
    add(f"before={report.product_id_hash_before[:16]}…")
    add(f"after ={report.product_id_hash_after[:16]}…")
    add("OK lecture seule" if report.product_id_hash_before == report.product_id_hash_after else "ERREUR")
    return "\n".join(lines)
