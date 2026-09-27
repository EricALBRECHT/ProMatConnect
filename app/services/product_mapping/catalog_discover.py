"""CATALOG DISCOVER — cartographie lecture seule d'un catalogue fournisseur.

Objectif : répondre à « que contient ce catalogue ? » sans rien écrire. Deux
étages, aucun moteur de matching dupliqué :

- étage A : les familles déjà industrialisées (CategoryRule enregistrées) sont
  reconnues via ``pipeline.extract_features`` + ``generic_matcher.best_match`` ;
- étage B : le reste est regroupé statistiquement, sans taxonomie BTP codée en
  dur (V1 : token dominant ; V2 : expressions discriminantes).

Invariants (V1 et V2) :
- aucune écriture (ni ``product_id``, ni Product, ni ProductCategory) — un
  snapshot des ``product_id`` est vérifié en fin de course ;
- supplier-neutre : la seule entrée métier est la liste des SupplierProduct ;
- déterminisme complet : à catalogue égal, rapport égal.

================================ ÉTAGE B V2 ================================

V1 nomme une famille par son token le plus fréquent, ce qui promeut des
matériaux (ACIER, BOIS, PVC) et des qualificatifs (INTERIEUR, DOUBLE) au rang
de familles produit. V2 garde toute la mécanique V1 (tokenisation, DF, seuils,
rapport, residual) et ajoute trois passes déterministes :

Discrimination — ``idf(t) = log((N+1)/(df(t)+1)) + 1`` sur le vivier de N SP
restants. Formule transparente, exposée telle quelle dans le rapport
(``frequency``, ``document_frequency``, ``idf``). Un token présent partout
tend vers 1.0 ; un token rare monte.

Passe 1, bigrammes d'abord — graines = bigrammes de tokens signifiants
consécutifs vus dans >= ``min_bigram_size`` SP (défaut : 60 % de
``min_cluster_size``). Un bigramme dont les DEUX côtés sont des couleurs /
finitions (``GENERIC_ATTRIBUTE_TOKENS``) est inéligible ; un bigramme n'ayant
qu'un côté attribut reste éligible mais passe après les bigrammes « purs » à
DF égale ou supérieure (« peinture acrylique » avant « acrylique blanche »).
Chaque SP rejoint la graine bigramme la mieux classée présente chez lui
(pureté, puis DF décroissante, puis alphabétique).

Passe 2, unigrammes pour le reste — exactement la logique V1 (``assign_clusters``)
appliquée aux SP non capturés par un bigramme, DF recalculée sur ce reste.

Passe 3, re-cluster local — un parent est redécoupé si sa taille atteint
``recluster_threshold`` (défaut 200) ou s'il s'agit d'un unigramme peu
discriminant. Les enfants sont les bigrammes locaux (calculés dans les seuls
membres du parent) atteignant ``child_min`` SP ; les membres restants forment
``residual_local``. La profondeur est plafonnée à 1 : un enfant n'a jamais
d'enfant.

head / qualifier — pour un bigramme « a b », le HEAD est le token de plus forte
IDF et le QUALIFIER l'autre ; en cas d'écart < ``HEAD_IDF_EPSILON`` le token de
gauche est le HEAD. Justification : un matériau ou une finition apparaît dans
beaucoup plus de désignations qu'une classe de produit, donc son IDF est plus
faible (« vis bois » → HEAD=vis, QUALIFIER=bois). Le label reste écrit dans
l'ordre naturel (« VIS BOIS »). Unigramme : head=token, qualifier=None.

``generic_cluster`` — vrai si AU MOINS une condition est remplie :
1. après re-cluster local, le premier enfant couvre < 40 % du parent ET plus de
   8 bigrammes locaux distincts atteignent ``child_min`` (dispersion) ;
2. ``technical_feature_coverage`` < 0.15 ET plus de 12 co-tokens distincts
   présents dans > 30 % des membres (le label n'explique pas ses membres) ;
3. le parent est un unigramme dont l'IDF est sous le seuil de démotion.
Exception : un parent bigramme de cohésion >= 0.9 non redécoupé est déclaré non
générique. Le seuil de démotion est la MÉDIANE des IDF des parents unigrammes
(échelle-libre, dérivée des données) ; s'il y a moins de 4 parents unigrammes,
il vaut 1.0 — c'est-à-dire aucune démotion, faute d'échantillon.

Aucune liste de matériaux, de familles ou de branches (« tube pvc »…) n'est
codée : seules les couleurs/finitions restent inéligibles comme graine, comme
en V1.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Supplier, SupplierProduct
from app.models.product_mapping import (
    CORRECTION_SOURCE_EXACT_RULE,
    CORRECTION_SOURCE_IMPORT,
    CORRECTION_SOURCE_MANUAL,
)
from app.services.product_mapping import pipeline
from app.services.product_mapping.generic_matcher import (
    REASON_AMBIGUOUS,
    REASON_EXACT,
    REASON_HIGH,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
    REASON_REVIEW,
    best_match,
)
from app.services.product_mapping.primitives import (
    extract_bar_length_mm,
    extract_diameter_length_mm,
    extract_dimensions_mm,
    extract_dn,
    extract_piece_count,
    extract_volume_ml,
    extract_weight_g,
    fold,
)
from app.services.product_mapping.rules import get_rule, registered_codes

DISCOVER_VERSION = "catalog_discover.v1"
DISCOVER_VERSION_V2 = "catalog_discover.v2"
DISCOVER_VERSIONS: dict[int, str] = {1: DISCOVER_VERSION, 2: DISCOVER_VERSION_V2}
DEFAULT_DISCOVER_VERSION = 2
CLI_DISCOVER_VERSION = 2

DEFAULT_MIN_CLUSTER_SIZE = 25
DEFAULT_TOP_CLUSTERS = 20
DEFAULT_EXAMPLE_LIMIT = 5
MIN_TOKEN_LENGTH = 3
TOP_TOKENS = 10
TOP_BIGRAMS = 10
TOP_FEATURE_VALUES = 5
RESIDUAL_EXAMPLES = 20

# --- Réglages V2 (tous exposés dans le rapport, aucun score opaque) --------
SEED_BIGRAM = "bigram"
SEED_UNIGRAM = "unigram"
# Un bigramme est plus exigeant qu'un unigramme : on abaisse son seuil pour ne
# pas rater « vis bois » quand « bois » domine largement.
MIN_BIGRAM_SIZE_RATIO = 0.6
MIN_BIGRAM_SIZE_FLOOR = 2
DEFAULT_RECLUSTER_THRESHOLD = 200
LOCAL_CHILD_MIN_FLOOR = 10
DEFAULT_TOP_ACTIONABLE = 30
TOP_DISCRIMINATION = 25
ACTIONABLE_MIN_COHESION = 0.85
ACTIONABLE_EXAMPLES_DETAILED = 10
GENERIC_TOP_CHILD_SHARE = 0.40
GENERIC_LOCAL_BIGRAM_COUNT = 8
GENERIC_FEATURE_COVERAGE = 0.15
GENERIC_CO_TOKEN_SHARE = 0.30
GENERIC_CO_TOKEN_COUNT = 12
BIGRAM_COHESION_OVERRIDE = 0.90
# Sous 4 parents unigrammes, la médiane des IDF n'a pas de sens : on neutralise
# la démotion (idf >= 1.0 par construction).
LOW_IDF_MIN_SEEDS = 4
LOW_IDF_NEUTRAL = 1.0
HEAD_IDF_EPSILON = 0.05
CHILD_RESIDUAL_LABEL = "(autres)"
RESIDUAL_PATH_LABEL = "(résidu)"
LEGACY_COMPARISON_LABELS: tuple[str, ...] = (
    "ACIER", "ALUMINIUM", "BOIS", "PEINTURE", "PORTE", "PVC", "TUBE", "VIS",
)
LEGACY_DESTINATION_LIMIT = 8
LEGACY_EXAMPLES = 3

# Mots outils / unités / conditionnements : jamais des familles produit.
# Constante module-level volontairement extensible (pas de taxonomie BTP ici).
FRENCH_STOPWORDS: frozenset[str] = frozenset(
    {
        "aux", "avec", "boite", "boites", "botte", "bottes", "carton", "cartons",
        "cet", "cette", "ces", "colis", "dans", "des", "dim", "dims", "environ",
        "est", "etc", "sont", "lot", "lots", "les", "mais", "pack", "packs",
        "paquet", "paquets", "par", "pas", "pce", "pces", "piece", "pieces",
        "plus", "pour", "qui", "que", "ref", "sachet", "sachets", "sans",
        "seau", "seaux", "ses", "soit", "son", "sous", "sur", "tres", "type",
        "types", "une", "unite", "unites", "kit", "coloris",
        # unités
        "cm", "kwh", "mm", "kilo", "litre", "litres", "metre", "metres", "nf",
    }
)

# Couleurs et finitions : qualificatifs transverses, jamais des familles. Sans
# cette liste, « blanc » (11 % du catalogue Brico Dépôt) agrège radiateurs,
# peintures, meubles et fenêtres dans une grappe illisible. Ces tokens restent
# comptés et affichés (top tokens / bigrammes) : ils sont seulement inéligibles
# comme GRAINE de cluster. Aucune taxonomie BTP n'est codée ici — matériaux et
# familles (acier, porte, peinture…) restent des graines légitimes.
GENERIC_ATTRIBUTE_TOKENS: frozenset[str] = frozenset(
    {
        "anthracite", "beige", "blanc", "blanche", "bleu", "bleue", "brillant",
        "brillante", "brun", "creme", "ecru", "gris", "grise", "incolore",
        "ivoire", "jaune", "mat", "mate", "marron", "noir", "noire", "orange",
        "rose", "rouge", "sable", "satin", "satine", "satinee", "taupe",
        "translucide", "transparent", "vert", "verte", "violet",
    }
)

# Un nombre nu (« 2500 », « 13 ») décrit une dimension, jamais une famille.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_DIGITS_RE = re.compile(r"^\d+$")

_CORRECTION_BUCKETS = (
    CORRECTION_SOURCE_EXACT_RULE,
    CORRECTION_SOURCE_MANUAL,
    CORRECTION_SOURCE_IMPORT,
)
CORRECTION_BUCKET_OTHER = "other"


# --------------------------------------------------------------------------
# Lecture du catalogue
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SupplierProductRow:
    """Vue minimale d'un SupplierProduct — suffisante pour extract_features."""

    id: int
    supplier_reference: str
    designation: str
    product_id: int | None
    correction_source: str | None
    brand: str | None = None

    @property
    def mapped(self) -> bool:
        return self.product_id is not None


def resolve_supplier_ids(session: Session, supplier_name: str | None) -> list[int]:
    """Fournisseur nommé (insensible à la casse) ou tout le référentiel."""
    if not supplier_name:
        return sorted(session.scalars(select(Supplier.id)).all())
    wanted = fold(supplier_name).replace("_", " ").replace("-", " ").strip()
    out = [
        s.id
        for s in session.scalars(select(Supplier).order_by(Supplier.id)).all()
        if fold(s.name).replace("_", " ").replace("-", " ").strip() == wanted
    ]
    return out


def load_supplier_products(
    session: Session, supplier_ids: Sequence[int]
) -> list[SupplierProductRow]:
    """Une seule requête, colonnes utiles seulement (~27k lignes en mémoire)."""
    if not supplier_ids:
        return []
    rows = session.execute(
        select(
            SupplierProduct.id,
            SupplierProduct.supplier_reference,
            SupplierProduct.designation,
            SupplierProduct.product_id,
            SupplierProduct.correction_source,
            SupplierProduct.brand,
        )
        .where(SupplierProduct.supplier_id.in_(list(supplier_ids)))
        .order_by(SupplierProduct.id)
    ).all()
    return [
        SupplierProductRow(
            id=r[0],
            supplier_reference=r[1] or "",
            designation=r[2] or "",
            product_id=r[3],
            correction_source=r[4],
            brand=r[5],
        )
        for r in rows
    ]


def product_id_snapshot_hash(rows: Iterable[SupplierProductRow]) -> str:
    """Empreinte (id, product_id) — garde-fou « aucune écriture »."""
    h = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r.id):
        h.update(f"{row.id}:{row.product_id}\n".encode())
    return h.hexdigest()


# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Tokens repliés (sans accent, minuscules), longueur >= MIN_TOKEN_LENGTH."""
    return [
        t
        for t in _TOKEN_RE.findall(fold(text or ""))
        if len(t) >= MIN_TOKEN_LENGTH
    ]


def content_tokens(text: str, *, keep_numbers: bool = False) -> list[str]:
    """Tokens signifiants : hors stopwords et (par défaut) hors nombres nus."""
    out = []
    for token in tokenize(text):
        if token in FRENCH_STOPWORDS:
            continue
        if not keep_numbers and _DIGITS_RE.match(token):
            continue
        out.append(token)
    return out


def bigrams(tokens: Sequence[str]) -> list[str]:
    """Bigrammes de tokens signifiants consécutifs."""
    return [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]


def _document_frequency(token_lists: Iterable[Sequence[str]]) -> Counter[str]:
    """Nombre de SP contenant le token (et non le nombre d'occurrences)."""
    counter: Counter[str] = Counter()
    for tokens in token_lists:
        counter.update(set(tokens))
    return counter


def _top(counter: Counter[str], limit: int) -> list[tuple[str, int]]:
    """Top stable : compte décroissant puis ordre alphabétique."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


# --------------------------------------------------------------------------
# Étage A — familles déjà connues (CategoryRule)
# --------------------------------------------------------------------------

@dataclass
class KnownCategoryStats:
    code: str
    pmc_candidates: int = 0
    classified: int = 0
    mapped: int = 0
    unmapped: int = 0
    exact: int = 0
    high: int = 0
    review: int = 0
    ambiguous: int = 0
    no_pmc_product: int = 0
    insufficient_data: int = 0
    correction_sources: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "pmc_candidates": self.pmc_candidates,
            "classified": self.classified,
            "mapped": self.mapped,
            "unmapped": self.unmapped,
            "exact": self.exact,
            "high": self.high,
            "review": self.review,
            "ambiguous": self.ambiguous,
            "no_pmc_product": self.no_pmc_product,
            "insufficient_data": self.insufficient_data,
            "correction_sources": {
                k: self.correction_sources[k] for k in sorted(self.correction_sources)
            },
        }


def _correction_bucket(source: str | None) -> str:
    if source in _CORRECTION_BUCKETS:
        return source
    return CORRECTION_BUCKET_OTHER


def classify_known_categories(
    session: Session,
    rows: Sequence[SupplierProductRow],
    *,
    category_codes: Sequence[str] | None = None,
) -> tuple[dict[str, KnownCategoryStats], dict[int, str]]:
    """Affecte chaque SP à la PREMIÈRE CategoryRule qui le classifie.

    Retourne (stats par code, assignation SP id → code). Aucun double comptage :
    l'ordre de tentative est ``sorted(registered_codes())``.
    """
    codes = tuple(category_codes) if category_codes else registered_codes()
    codes = tuple(sorted(codes))
    rules = [get_rule(code) for code in codes]
    candidates = {
        code: pipeline.load_pmc_candidates(session, rule)
        for code, rule in zip(codes, rules)
    }
    stats = {
        code: KnownCategoryStats(code=code, pmc_candidates=len(candidates[code]))
        for code in codes
    }
    assigned: dict[int, str] = {}

    for row in rows:
        for code, rule in zip(codes, rules):
            features = pipeline.extract_features(rule, row)
            if not features.classified:
                continue

            stat = stats[code]
            assigned[row.id] = code
            stat.classified += 1

            if row.mapped:
                stat.mapped += 1
                bucket = _correction_bucket(row.correction_source)
                stat.correction_sources[bucket] = (
                    stat.correction_sources.get(bucket, 0) + 1
                )
                break

            stat.unmapped += 1
            match = best_match(rule, features.as_attrs(), candidates[code])
            reason = match.reason or REASON_NO_PMC_PRODUCT
            if reason == REASON_EXACT:
                stat.exact += 1
            elif reason == REASON_HIGH:
                stat.high += 1
            elif reason == REASON_REVIEW:
                stat.review += 1
            elif reason == REASON_AMBIGUOUS:
                stat.ambiguous += 1
            elif reason == REASON_INSUFFICIENT_DATA:
                stat.insufficient_data += 1
            else:
                stat.no_pmc_product += 1
            break

    return stats, assigned


# --------------------------------------------------------------------------
# Étage B — découverte
# --------------------------------------------------------------------------

FEATURE_DIMENSIONS = "dimensions_lxwxt_mm"
FEATURE_DIAMETER_LENGTH = "diameter_x_length_mm"
FEATURE_BAR_LENGTH = "bar_length_mm"
FEATURE_PIECE_COUNT = "piece_count"
FEATURE_VOLUME_ML = "volume_ml"
FEATURE_WEIGHT_G = "weight_g"
FEATURE_DN = "dn"

FEATURE_KEYS = (
    FEATURE_DIMENSIONS,
    FEATURE_DIAMETER_LENGTH,
    FEATURE_BAR_LENGTH,
    FEATURE_PIECE_COUNT,
    FEATURE_VOLUME_ML,
    FEATURE_WEIGHT_G,
    FEATURE_DN,
)


def detect_features(designation: str) -> dict[str, str]:
    """Primitives techniques réutilisées — clé → valeur lisible (variante)."""
    out: dict[str, str] = {}
    length, width, thickness = extract_dimensions_mm(designation)
    if length is not None and width is not None and thickness is not None:
        out[FEATURE_DIMENSIONS] = f"{length}x{width}x{thickness}"
    diameter, screw_length = extract_diameter_length_mm(designation)
    if diameter is not None and screw_length is not None:
        out[FEATURE_DIAMETER_LENGTH] = f"{diameter}x{screw_length}"
    bar = extract_bar_length_mm(designation)
    if bar is not None:
        out[FEATURE_BAR_LENGTH] = str(bar)
    pieces = extract_piece_count(designation)
    if pieces is not None:
        out[FEATURE_PIECE_COUNT] = str(pieces)
    volume = extract_volume_ml(designation)
    if volume is not None:
        out[FEATURE_VOLUME_ML] = str(volume)
    weight = extract_weight_g(designation)
    if weight is not None:
        out[FEATURE_WEIGHT_G] = str(weight)
    dn = extract_dn(designation)
    if dn is not None:
        out[FEATURE_DN] = str(dn)
    return out


@dataclass
class FeatureStat:
    key: str
    sp_count: int
    top_values: list[tuple[str, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "sp_count": self.sp_count,
            "top_values": [{"value": v, "count": n} for v, n in self.top_values],
        }


@dataclass
class ExampleSP:
    id: int
    sku: str
    designation: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "sku": self.sku, "designation": self.designation}


@dataclass
class ClusterReport:
    label: str
    seed_token: str
    sp_count: int
    mapped: int
    unmapped: int
    top_tokens: list[tuple[str, int]] = field(default_factory=list)
    top_bigrams: list[tuple[str, int]] = field(default_factory=list)
    label_cohesion: float = 0.0
    top3_cohesion: float = 0.0
    technical_feature_sp: int = 0
    technical_feature_coverage: float = 0.0
    features: list[FeatureStat] = field(default_factory=list)
    examples: list[ExampleSP] = field(default_factory=list)
    rule_exists: bool = False
    existing_pmc_candidates: int | None = None
    # --- Champs V2 (absents du schéma JSON V1) ---------------------------
    seed_kind: str = SEED_UNIGRAM
    head: str | None = None
    qualifier: str | None = None
    document_frequency: int = 0
    discrimination: float = 0.0
    co_token_count: int = 0
    distinct_local_bigrams: int = 0
    top_child_share: float = 0.0
    generic_cluster: bool = False
    children: list["ClusterReport"] = field(default_factory=list)
    residual_local_count: int = 0
    residual_local_examples: list[ExampleSP] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "seed_token": self.seed_token,
            "sp_count": self.sp_count,
            "mapped": self.mapped,
            "unmapped": self.unmapped,
            "top_tokens": [{"token": t, "count": n} for t, n in self.top_tokens],
            "top_bigrams": [{"bigram": b, "count": n} for b, n in self.top_bigrams],
            "label_cohesion": self.label_cohesion,
            "top3_cohesion": self.top3_cohesion,
            "technical_feature_sp": self.technical_feature_sp,
            "technical_feature_coverage": self.technical_feature_coverage,
            "features": [f.to_dict() for f in self.features],
            "examples": [e.to_dict() for e in self.examples],
            "rule_exists": self.rule_exists,
            "existing_pmc_candidates": self.existing_pmc_candidates,
        }

    def to_dict_v2(self) -> dict[str, Any]:
        """Schéma V2 : clés V1 inchangées + hiérarchie et discrimination."""
        payload = self.to_dict()
        payload.update(
            {
                "seed_kind": self.seed_kind,
                "head": self.head,
                "qualifier": self.qualifier,
                "document_frequency": self.document_frequency,
                "idf": self.discrimination,
                "discrimination": self.discrimination,
                "co_token_count": self.co_token_count,
                "distinct_local_bigrams": self.distinct_local_bigrams,
                "top_child_share": self.top_child_share,
                "generic_cluster": self.generic_cluster,
                "children": [c.to_dict_v2() for c in self.children],
                "residual_local_count": self.residual_local_count,
                "residual_local_examples": [
                    e.to_dict() for e in self.residual_local_examples
                ],
            }
        )
        return payload


@dataclass
class ResidualReport:
    sp_count: int = 0
    top_tokens: list[tuple[str, int]] = field(default_factory=list)
    examples: list[ExampleSP] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sp_count": self.sp_count,
            "top_tokens": [{"token": t, "count": n} for t, n in self.top_tokens],
            "examples": [e.to_dict() for e in self.examples],
        }


@dataclass
class CoverageSummary:
    total: int = 0
    known_category_sp: int = 0
    discovery_cluster_sp: int = 0
    residual: int = 0
    cluster_count: int = 0
    duration_ms: int = 0
    sp_per_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "known_category_sp": self.known_category_sp,
            "discovery_cluster_sp": self.discovery_cluster_sp,
            "residual": self.residual,
            "cluster_count": self.cluster_count,
            "duration_ms": self.duration_ms,
            "sp_per_sec": self.sp_per_sec,
        }


@dataclass
class PriorityRow:
    family: str
    supplier_products: int
    unmapped: int
    dominant_token_coverage: float
    technical_feature_coverage: float
    mapped: int
    rule_exists: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "supplier_products": self.supplier_products,
            "unmapped": self.unmapped,
            "dominant_token_coverage": self.dominant_token_coverage,
            "technical_feature_coverage": self.technical_feature_coverage,
            "mapped": self.mapped,
            "rule_exists": self.rule_exists,
        }


@dataclass
class ActionableFamily:
    """Famille exploitable — vue aplatie (parents non génériques + enfants)."""

    family: str
    path: str
    seed_token: str
    seed_kind: str
    head: str | None
    qualifier: str | None
    sp_count: int
    mapped: int
    unmapped: int
    discrimination: float
    label_cohesion: float
    technical_feature_coverage: float
    dominant_attributes: list[tuple[str, int]] = field(default_factory=list)
    dominant_bigrams: list[tuple[str, int]] = field(default_factory=list)
    rule_exists: bool = False
    examples: list[ExampleSP] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "path": self.path,
            "seed_token": self.seed_token,
            "seed_kind": self.seed_kind,
            "head": self.head,
            "qualifier": self.qualifier,
            "sp_count": self.sp_count,
            "mapped": self.mapped,
            "unmapped": self.unmapped,
            "discrimination": self.discrimination,
            "label_cohesion": self.label_cohesion,
            "technical_feature_coverage": self.technical_feature_coverage,
            "dominant_attributes": [
                {"key": k, "sp_count": n} for k, n in self.dominant_attributes
            ],
            "dominant_bigrams": [
                {"bigram": b, "count": n} for b, n in self.dominant_bigrams
            ],
            "rule_exists": self.rule_exists,
            "examples": [e.to_dict() for e in self.examples],
        }


@dataclass
class DiscriminationRow:
    """Ligne du registre de discrimination — aucun score dérivé opaque."""

    term: str
    frequency: int
    document_frequency: int
    idf: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "frequency": self.frequency,
            "document_frequency": self.document_frequency,
            "idf": self.idf,
        }


@dataclass
class LegacyDestination:
    path: str
    sp_count: int
    share: float
    examples: list[ExampleSP] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sp_count": self.sp_count,
            "share": self.share,
            "examples": [e.to_dict() for e in self.examples],
        }


@dataclass
class LegacyComparison:
    """Devenir d'un cluster V1 dans la hiérarchie V2, SP par SP."""

    label: str
    seed_token: str
    v1_sp_count: int
    v2_destination_count: int = 0
    destinations: list[LegacyDestination] = field(default_factory=list)
    residual_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "seed_token": self.seed_token,
            "v1_sp_count": self.v1_sp_count,
            "v2_destination_count": self.v2_destination_count,
            "destinations": [d.to_dict() for d in self.destinations],
            "residual_count": self.residual_count,
        }


@dataclass
class DiscoverReport:
    version: str = DISCOVER_VERSION
    supplier: str | None = None
    supplier_ids: list[int] = field(default_factory=list)
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE
    top_clusters: int = DEFAULT_TOP_CLUSTERS
    example_limit: int = DEFAULT_EXAMPLE_LIMIT
    coverage: CoverageSummary = field(default_factory=CoverageSummary)
    known_categories: list[KnownCategoryStats] = field(default_factory=list)
    prioritization: list[PriorityRow] = field(default_factory=list)
    clusters: list[ClusterReport] = field(default_factory=list)
    residual: ResidualReport = field(default_factory=ResidualReport)
    product_id_hash_before: str = ""
    product_id_hash_after: str = ""
    # --- Champs V2 --------------------------------------------------------
    min_bigram_size: int = 0
    recluster_threshold: int = DEFAULT_RECLUSTER_THRESHOLD
    low_idf_threshold: float = LOW_IDF_NEUTRAL
    top_actionable: int = DEFAULT_TOP_ACTIONABLE
    subcluster_count: int = 0
    generic_cluster_count: int = 0
    actionable_families: list[ActionableFamily] = field(default_factory=list)
    token_discrimination: list[DiscriminationRow] = field(default_factory=list)
    bigram_discrimination: list[DiscriminationRow] = field(default_factory=list)
    v1_comparison: list[LegacyComparison] = field(default_factory=list)

    @property
    def is_v2(self) -> bool:
        return self.version == DISCOVER_VERSION_V2

    def to_dict(self) -> dict[str, Any]:
        limit = self.top_clusters
        payload = {
            "version": self.version,
            "supplier": self.supplier,
            "supplier_ids": self.supplier_ids,
            "min_cluster_size": self.min_cluster_size,
            "top_clusters": self.top_clusters,
            "example_limit": self.example_limit,
            "coverage": self.coverage.to_dict(),
            "known_categories": [k.to_dict() for k in self.known_categories],
            "prioritization": [p.to_dict() for p in self.prioritization[:limit]],
            "clusters": [c.to_dict() for c in self.clusters[:limit]],
            "residual": self.residual.to_dict(),
            "product_id_hash_before": self.product_id_hash_before,
            "product_id_hash_after": self.product_id_hash_after,
        }
        if not self.is_v2:
            return payload
        payload["clusters"] = [c.to_dict_v2() for c in self.clusters[:limit]]
        payload.update(
            {
                "min_bigram_size": self.min_bigram_size,
                "recluster_threshold": self.recluster_threshold,
                "low_idf_threshold": self.low_idf_threshold,
                "top_actionable": self.top_actionable,
                "subcluster_count": self.subcluster_count,
                "generic_cluster_count": self.generic_cluster_count,
                "actionable_families": [
                    a.to_dict() for a in self.actionable_families[: self.top_actionable]
                ],
                "token_discrimination": [
                    d.to_dict() for d in self.token_discrimination
                ],
                "bigram_discrimination": [
                    d.to_dict() for d in self.bigram_discrimination
                ],
                "v1_comparison": [c.to_dict() for c in self.v1_comparison],
            }
        )
        return payload


def assign_clusters(
    rows: Sequence[SupplierProductRow],
    *,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> tuple[dict[str, list[SupplierProductRow]], list[SupplierProductRow], dict[int, list[str]]]:
    """Regroupement déterministe par token dominant.

    1. graines = tokens présents dans >= ``min_cluster_size`` SP, hors couleurs
       et finitions (qualificatifs transverses) ;
    2. chaque SP rejoint la graine présente chez lui dont le compte global est
       le plus élevé (égalité → ordre alphabétique) ;
    3. les grappes retombées sous le seuil sont dissoutes vers le résidu.
    """
    tokens_by_sp: dict[int, list[str]] = {
        row.id: content_tokens(row.designation) for row in rows
    }
    global_counts = _document_frequency(tokens_by_sp.values())
    seeds = {
        t
        for t, n in global_counts.items()
        if n >= min_cluster_size and t not in GENERIC_ATTRIBUTE_TOKENS
    }

    clusters: dict[str, list[SupplierProductRow]] = {}
    residual: list[SupplierProductRow] = []
    for row in rows:
        present = seeds.intersection(tokens_by_sp[row.id])
        if not present:
            residual.append(row)
            continue
        seed = min(present, key=lambda t: (-global_counts[t], t))
        clusters.setdefault(seed, []).append(row)

    for seed in sorted(clusters):
        if len(clusters[seed]) < min_cluster_size:
            residual.extend(clusters.pop(seed))
    residual.sort(key=lambda r: r.id)
    return clusters, residual, tokens_by_sp


# --------------------------------------------------------------------------
# Étage B V2 — discrimination, bigrammes prioritaires, re-cluster local
# --------------------------------------------------------------------------

def idf(document_frequency: int, doc_count: int) -> float:
    """``log((N+1)/(df+1)) + 1`` — 1.0 quand le terme est partout, croît sinon."""
    if doc_count <= 0:
        return 0.0
    return round(math.log((doc_count + 1) / (document_frequency + 1)) + 1.0, 4)


def default_min_bigram_size(min_cluster_size: int) -> int:
    """Seuil bigramme : 60 % du seuil unigramme (25 → 15), plancher 2."""
    return max(
        MIN_BIGRAM_SIZE_FLOOR, int(round(min_cluster_size * MIN_BIGRAM_SIZE_RATIO))
    )


def default_child_min_size(min_cluster_size: int) -> int:
    """Seuil d'un enfant local : moitié du seuil parent, plancher 10 (ou moins
    si le seuil parent est lui-même plus petit — catalogues de test)."""
    return max(min(LOCAL_CHILD_MIN_FLOOR, min_cluster_size), min_cluster_size // 2)


def _bigram_sides(bigram: str) -> tuple[str, str]:
    left, _, right = bigram.partition(" ")
    return left, right


def _bigram_seed_eligible(bigram: str) -> bool:
    """Inéligible si les DEUX côtés sont des couleurs/finitions (« blanc mat »)."""
    left, right = _bigram_sides(bigram)
    return not (
        left in GENERIC_ATTRIBUTE_TOKENS and right in GENERIC_ATTRIBUTE_TOKENS
    )


def _bigram_has_attribute(bigram: str) -> bool:
    left, right = _bigram_sides(bigram)
    return left in GENERIC_ATTRIBUTE_TOKENS or right in GENERIC_ATTRIBUTE_TOKENS


def head_and_qualifier(
    seed: str, seed_kind: str, idf_of_token: Any
) -> tuple[str, str | None]:
    """HEAD = côté le plus discriminant (IDF), QUALIFIER = l'autre.

    Un matériau ou une finition apparaît dans beaucoup plus de désignations
    qu'une classe de produit : son IDF est donc plus faible. À écart d'IDF
    inférieur à ``HEAD_IDF_EPSILON``, le token de gauche est retenu comme HEAD.
    """
    if seed_kind != SEED_BIGRAM:
        return seed, None
    left, right = _bigram_sides(seed)
    if not right:
        return left, None
    if idf_of_token(right) - idf_of_token(left) > HEAD_IDF_EPSILON:
        return right, left
    return left, right


def _frequencies(
    term_lists: Iterable[Sequence[str]],
) -> tuple[Counter[str], Counter[str]]:
    """(occurrences brutes, nombre de documents) en une seule passe O(N)."""
    frequency: Counter[str] = Counter()
    document: Counter[str] = Counter()
    for terms in term_lists:
        frequency.update(terms)
        document.update(set(terms))
    return frequency, document


@dataclass
class ClusterGroup:
    """Grappe V2 avant mise en forme : parent + enfants locaux éventuels."""

    seed: str
    seed_kind: str
    members: list[SupplierProductRow]
    children: dict[str, list[SupplierProductRow]] = field(default_factory=dict)
    residual_local: list[SupplierProductRow] = field(default_factory=list)
    local_bigram_seeds: int = 0
    split: bool = False

    @property
    def label(self) -> str:
        return self.seed.upper()


@dataclass
class DiscoveryAssignment:
    """Sortie brute de ``assign_clusters_v2`` — statistiques réutilisables."""

    groups: list[ClusterGroup]
    residual: list[SupplierProductRow]
    tokens_by_sp: dict[int, list[str]]
    bigrams_by_sp: dict[int, list[str]]
    token_frequency: Counter[str]
    token_df: Counter[str]
    bigram_frequency: Counter[str]
    bigram_df: Counter[str]
    doc_count: int
    min_bigram_size: int
    child_min_size: int
    low_idf_threshold: float = LOW_IDF_NEUTRAL

    def idf_of_token(self, token: str) -> float:
        return idf(self.token_df[token], self.doc_count)

    def seed_df(self, group: ClusterGroup) -> int:
        if group.seed_kind == SEED_BIGRAM:
            return self.bigram_df[group.seed]
        return self.token_df[group.seed]

    def seed_idf(self, group: ClusterGroup) -> float:
        return idf(self.seed_df(group), self.doc_count)


def _split_group_locally(
    group: ClusterGroup,
    bigrams_by_sp: dict[int, list[str]],
    *,
    child_min_size: int,
) -> None:
    """Re-cluster local : enfants = bigrammes observés DANS les seuls membres."""
    group.split = True
    local_df: Counter[str] = Counter()
    for row in group.members:
        local_df.update(set(bigrams_by_sp[row.id]))
    seeds = {
        b
        for b, count in local_df.items()
        if count >= child_min_size and b != group.seed and _bigram_seed_eligible(b)
    }
    group.local_bigram_seeds = len(seeds)
    if not seeds:
        group.residual_local = list(group.members)
        return

    def rank(bigram: str) -> tuple[int, int, str]:
        return (1 if _bigram_has_attribute(bigram) else 0, -local_df[bigram], bigram)

    children: dict[str, list[SupplierProductRow]] = {}
    residual_local: list[SupplierProductRow] = []
    for row in group.members:
        present = seeds.intersection(bigrams_by_sp[row.id])
        if not present:
            residual_local.append(row)
            continue
        children.setdefault(min(present, key=rank), []).append(row)
    for seed in sorted(children):
        if len(children[seed]) < child_min_size:
            residual_local.extend(children.pop(seed))
    residual_local.sort(key=lambda r: r.id)
    group.children = {seed: children[seed] for seed in sorted(children)}
    group.residual_local = residual_local


def assign_clusters_v2(
    rows: Sequence[SupplierProductRow],
    *,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    min_bigram_size: int | None = None,
    recluster_threshold: int = DEFAULT_RECLUSTER_THRESHOLD,
    child_min_size: int | None = None,
) -> DiscoveryAssignment:
    """Regroupement V2 déterministe en trois passes O(N).

    1. bigrammes discriminants (graine préférée) ;
    2. unigrammes façon V1 sur les SP restants ;
    3. re-cluster local des parents volumineux ou peu discriminants.
    """
    tokens_by_sp: dict[int, list[str]] = {
        row.id: content_tokens(row.designation) for row in rows
    }
    bigrams_by_sp: dict[int, list[str]] = {
        sp_id: bigrams(tokens) for sp_id, tokens in tokens_by_sp.items()
    }
    token_frequency, token_df = _frequencies(tokens_by_sp.values())
    bigram_frequency, bigram_df = _frequencies(bigrams_by_sp.values())
    doc_count = len(rows)
    if min_bigram_size is None:
        min_bigram_size = default_min_bigram_size(min_cluster_size)
    if child_min_size is None:
        child_min_size = default_child_min_size(min_cluster_size)

    # --- Passe 1 : bigrammes -------------------------------------------
    bigram_seeds = {
        b
        for b, count in bigram_df.items()
        if count >= min_bigram_size and _bigram_seed_eligible(b)
    }

    def bigram_rank(bigram: str) -> tuple[int, int, str]:
        return (1 if _bigram_has_attribute(bigram) else 0, -bigram_df[bigram], bigram)

    bigram_members: dict[str, list[SupplierProductRow]] = {}
    leftovers: list[SupplierProductRow] = []
    for row in rows:
        present = bigram_seeds.intersection(bigrams_by_sp[row.id])
        if not present:
            leftovers.append(row)
            continue
        bigram_members.setdefault(min(present, key=bigram_rank), []).append(row)
    for seed in sorted(bigram_members):
        if len(bigram_members[seed]) < min_bigram_size:
            leftovers.extend(bigram_members.pop(seed))
    leftovers.sort(key=lambda r: r.id)

    # --- Passe 2 : unigrammes sur le reste (logique V1) -----------------
    leftover_df: Counter[str] = Counter()
    for row in leftovers:
        leftover_df.update(set(tokens_by_sp[row.id]))
    unigram_seeds = {
        t
        for t, count in leftover_df.items()
        if count >= min_cluster_size and t not in GENERIC_ATTRIBUTE_TOKENS
    }
    unigram_members: dict[str, list[SupplierProductRow]] = {}
    residual: list[SupplierProductRow] = []
    for row in leftovers:
        present = unigram_seeds.intersection(tokens_by_sp[row.id])
        if not present:
            residual.append(row)
            continue
        seed = min(present, key=lambda t: (-leftover_df[t], t))
        unigram_members.setdefault(seed, []).append(row)
    for seed in sorted(unigram_members):
        if len(unigram_members[seed]) < min_cluster_size:
            residual.extend(unigram_members.pop(seed))
    residual.sort(key=lambda r: r.id)

    groups = [
        ClusterGroup(seed=seed, seed_kind=SEED_BIGRAM, members=bigram_members[seed])
        for seed in sorted(bigram_members)
    ] + [
        ClusterGroup(seed=seed, seed_kind=SEED_UNIGRAM, members=unigram_members[seed])
        for seed in sorted(unigram_members)
    ]

    assignment = DiscoveryAssignment(
        groups=groups,
        residual=residual,
        tokens_by_sp=tokens_by_sp,
        bigrams_by_sp=bigrams_by_sp,
        token_frequency=token_frequency,
        token_df=token_df,
        bigram_frequency=bigram_frequency,
        bigram_df=bigram_df,
        doc_count=doc_count,
        min_bigram_size=min_bigram_size,
        child_min_size=child_min_size,
    )

    # Seuil de démotion : médiane des IDF des parents unigrammes.
    unigram_idfs = [
        assignment.seed_idf(group)
        for group in groups
        if group.seed_kind == SEED_UNIGRAM
    ]
    assignment.low_idf_threshold = (
        round(statistics.median(unigram_idfs), 4)
        if len(unigram_idfs) >= LOW_IDF_MIN_SEEDS
        else LOW_IDF_NEUTRAL
    )

    # --- Passe 3 : re-cluster local ------------------------------------
    for group in groups:
        low_discrimination = (
            group.seed_kind == SEED_UNIGRAM
            and assignment.seed_idf(group) < assignment.low_idf_threshold
        )
        large = len(group.members) >= recluster_threshold
        splittable = len(group.members) >= 2 * child_min_size
        if large or (low_discrimination and splittable):
            _split_group_locally(group, bigrams_by_sp, child_min_size=child_min_size)
    return assignment


def _build_cluster_report(
    seed: str,
    members: Sequence[SupplierProductRow],
    tokens_by_sp: dict[int, list[str]],
    *,
    example_limit: int,
    seed_kind: str = SEED_UNIGRAM,
    document_frequency: int = 0,
    discrimination: float = 0.0,
    head: str | None = None,
    qualifier: str | None = None,
) -> ClusterReport:
    token_counts = _document_frequency(tokens_by_sp[r.id] for r in members)
    bigram_counts = _document_frequency(
        bigrams(tokens_by_sp[r.id]) for r in members
    )
    top_tokens = _top(token_counts, TOP_TOKENS)
    top3 = {t for t, _ in top_tokens[:3]}

    feature_sp: Counter[str] = Counter()
    feature_values: dict[str, Counter[str]] = {k: Counter() for k in FEATURE_KEYS}
    technical = 0
    with_top3 = 0
    for row in members:
        if top3.intersection(tokens_by_sp[row.id]):
            with_top3 += 1
        detected = detect_features(row.designation)
        if detected:
            technical += 1
        for key, value in detected.items():
            feature_sp[key] += 1
            feature_values[key][value] += 1

    total = len(members)
    seed_parts = set(seed.split(" "))
    with_label = (
        bigram_counts[seed] if seed_kind == SEED_BIGRAM else token_counts[seed]
    )
    # Dispersion des cooccurrences : tokens (hors graine) partagés par une large
    # part des membres. Beaucoup de co-tokens = label qui n'explique pas ses SP.
    co_token_floor = GENERIC_CO_TOKEN_SHARE * total
    co_token_count = sum(
        1
        for token, count in token_counts.items()
        if token not in seed_parts and count > co_token_floor
    )
    mapped = sum(1 for r in members if r.mapped)
    return ClusterReport(
        label=seed.upper(),
        seed_token=seed,
        sp_count=total,
        mapped=mapped,
        unmapped=total - mapped,
        top_tokens=top_tokens,
        top_bigrams=_top(bigram_counts, TOP_BIGRAMS),
        label_cohesion=round(with_label / total, 4) if total else 0.0,
        top3_cohesion=round(with_top3 / total, 4) if total else 0.0,
        technical_feature_sp=technical,
        technical_feature_coverage=round(technical / total, 4) if total else 0.0,
        features=[
            FeatureStat(
                key=key,
                sp_count=feature_sp[key],
                top_values=_top(feature_values[key], TOP_FEATURE_VALUES),
            )
            for key in FEATURE_KEYS
            if feature_sp[key]
        ],
        examples=[
            ExampleSP(id=r.id, sku=r.supplier_reference, designation=r.designation)
            for r in sorted(members, key=lambda r: r.id)[:example_limit]
        ],
        # Les familles déjà couvertes par une CategoryRule sont sorties à l'étage A.
        rule_exists=False,
        # Pas de lien PMC « flou » inventé : seul le mapped du cluster est factuel.
        existing_pmc_candidates=None,
        seed_kind=seed_kind,
        head=head,
        qualifier=qualifier,
        document_frequency=document_frequency,
        discrimination=discrimination,
        co_token_count=co_token_count,
    )


def _examples(
    rows: Sequence[SupplierProductRow], limit: int
) -> list[ExampleSP]:
    return [
        ExampleSP(id=r.id, sku=r.supplier_reference, designation=r.designation)
        for r in sorted(rows, key=lambda r: r.id)[:limit]
    ]


def is_generic_cluster(
    cluster: ClusterReport, *, low_idf_threshold: float, split: bool
) -> bool:
    """Heuristique ``generic_cluster`` — voir le docstring du module (3 règles)."""
    if (
        cluster.seed_kind == SEED_BIGRAM
        and cluster.label_cohesion >= BIGRAM_COHESION_OVERRIDE
        and not split
    ):
        return False
    if (
        split
        and cluster.top_child_share < GENERIC_TOP_CHILD_SHARE
        and cluster.distinct_local_bigrams > GENERIC_LOCAL_BIGRAM_COUNT
    ):
        return True
    if (
        cluster.technical_feature_coverage < GENERIC_FEATURE_COVERAGE
        and cluster.co_token_count > GENERIC_CO_TOKEN_COUNT
    ):
        return True
    return (
        cluster.seed_kind == SEED_UNIGRAM
        and cluster.discrimination < low_idf_threshold
    )


def build_v2_cluster_reports(
    assignment: DiscoveryAssignment, *, example_limit: int
) -> list[ClusterReport]:
    """Parents triés par non mappés décroissants, enfants par taille."""
    reports: list[ClusterReport] = []
    for group in assignment.groups:
        head, qualifier = head_and_qualifier(
            group.seed, group.seed_kind, assignment.idf_of_token
        )
        parent = _build_cluster_report(
            group.seed,
            group.members,
            assignment.tokens_by_sp,
            example_limit=example_limit,
            seed_kind=group.seed_kind,
            document_frequency=assignment.seed_df(group),
            discrimination=assignment.seed_idf(group),
            head=head,
            qualifier=qualifier,
        )
        for child_seed, child_members in group.children.items():
            child_head, child_qualifier = head_and_qualifier(
                child_seed, SEED_BIGRAM, assignment.idf_of_token
            )
            child = _build_cluster_report(
                child_seed,
                child_members,
                assignment.tokens_by_sp,
                example_limit=example_limit,
                seed_kind=SEED_BIGRAM,
                document_frequency=assignment.bigram_df[child_seed],
                discrimination=idf(
                    assignment.bigram_df[child_seed], assignment.doc_count
                ),
                head=child_head,
                qualifier=child_qualifier,
            )
            # Profondeur plafonnée à 1 : un enfant n'est jamais redécoupé.
            child.generic_cluster = is_generic_cluster(
                child, low_idf_threshold=assignment.low_idf_threshold, split=False
            )
            parent.children.append(child)
        parent.children.sort(key=lambda c: (-c.sp_count, c.seed_token))
        parent.distinct_local_bigrams = group.local_bigram_seeds
        parent.residual_local_count = len(group.residual_local)
        parent.residual_local_examples = _examples(group.residual_local, example_limit)
        if parent.children and parent.sp_count:
            parent.top_child_share = round(
                parent.children[0].sp_count / parent.sp_count, 4
            )
        parent.generic_cluster = is_generic_cluster(
            parent, low_idf_threshold=assignment.low_idf_threshold, split=group.split
        )
        reports.append(parent)
    reports.sort(key=lambda c: (-c.unmapped, -c.sp_count, c.seed_token))
    return reports


def is_actionable_family(
    cluster: ClusterReport, *, min_cluster_size: int, low_idf_threshold: float
) -> bool:
    """Exploitable : cohésif, non générique, volume réel, label discriminant."""
    if cluster.generic_cluster:
        return False
    if cluster.sp_count < min_cluster_size:
        return False
    if cluster.label_cohesion < ACTIONABLE_MIN_COHESION:
        return False
    return (
        cluster.seed_kind == SEED_BIGRAM
        or cluster.discrimination >= low_idf_threshold
    )


def build_actionable_families(
    clusters: Sequence[ClusterReport],
    *,
    min_cluster_size: int,
    low_idf_threshold: float,
    example_limit: int,
) -> list[ActionableFamily]:
    """Vue aplatie : enfants des parents génériques, parents cohésifs sinon."""
    families: list[ActionableFamily] = []
    for parent in clusters:
        if parent.generic_cluster:
            candidates = [
                (f"{parent.label} > {child.label}", child) for child in parent.children
            ]
        else:
            candidates = [(parent.label, parent)]
        for path, cluster in candidates:
            if not is_actionable_family(
                cluster,
                min_cluster_size=min_cluster_size,
                low_idf_threshold=low_idf_threshold,
            ):
                continue
            families.append(
                ActionableFamily(
                    family=cluster.label,
                    path=path,
                    seed_token=cluster.seed_token,
                    seed_kind=cluster.seed_kind,
                    head=cluster.head,
                    qualifier=cluster.qualifier,
                    sp_count=cluster.sp_count,
                    mapped=cluster.mapped,
                    unmapped=cluster.unmapped,
                    discrimination=cluster.discrimination,
                    label_cohesion=cluster.label_cohesion,
                    technical_feature_coverage=cluster.technical_feature_coverage,
                    dominant_attributes=sorted(
                        ((f.key, f.sp_count) for f in cluster.features),
                        key=lambda kv: (-kv[1], kv[0]),
                    ),
                    dominant_bigrams=cluster.top_bigrams[:3],
                    rule_exists=cluster.rule_exists,
                    examples=cluster.examples[:example_limit],
                )
            )
    families.sort(key=lambda f: (-f.unmapped, -f.sp_count, f.path))
    return families


def discrimination_ledger(
    frequency: Counter[str], document: Counter[str], doc_count: int, limit: int
) -> list[DiscriminationRow]:
    """Registre lisible : occurrences, documents, IDF — trié par DF puis alpha."""
    return [
        DiscriminationRow(
            term=term,
            frequency=frequency[term],
            document_frequency=count,
            idf=idf(count, doc_count),
        )
        for term, count in _top(document, limit)
    ]


def v2_destination_paths(assignment: DiscoveryAssignment) -> dict[int, str]:
    """SP id → chemin V2 (« PVC > TUBE PVC », « PVC > (autres) », « (résidu) »)."""
    paths: dict[int, str] = {}
    for group in assignment.groups:
        if group.children:
            for child_seed, members in group.children.items():
                path = f"{group.label} > {child_seed.upper()}"
                for row in members:
                    paths[row.id] = path
            for row in group.residual_local:
                paths[row.id] = f"{group.label} > {CHILD_RESIDUAL_LABEL}"
        elif group.split:
            for row in group.members:
                paths[row.id] = f"{group.label} > {CHILD_RESIDUAL_LABEL}"
        else:
            for row in group.members:
                paths[row.id] = group.label
    for row in assignment.residual:
        paths[row.id] = RESIDUAL_PATH_LABEL
    return paths


def compare_legacy_clusters(
    remainder: Sequence[SupplierProductRow],
    assignment: DiscoveryAssignment,
    *,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    labels: Sequence[str] = LEGACY_COMPARISON_LABELS,
    example_limit: int = LEGACY_EXAMPLES,
    destination_limit: int = LEGACY_DESTINATION_LIMIT,
) -> list[LegacyComparison]:
    """Devenir des gros clusters V1 : une passe ``assign_clusters`` V1 en plus.

    Le coût est celui d'une passe O(N) supplémentaire sur le même vivier — pas
    d'un second rapport complet.
    """
    v1_clusters, _, _ = assign_clusters(remainder, min_cluster_size=min_cluster_size)
    paths = v2_destination_paths(assignment)
    comparisons: list[LegacyComparison] = []
    for label in labels:
        seed = fold(label)
        members = v1_clusters.get(seed)
        if not members:
            comparisons.append(
                LegacyComparison(label=label, seed_token=seed, v1_sp_count=0)
            )
            continue
        buckets: dict[str, list[SupplierProductRow]] = {}
        for row in members:
            buckets.setdefault(paths.get(row.id, RESIDUAL_PATH_LABEL), []).append(row)
        ordered = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        comparisons.append(
            LegacyComparison(
                label=label,
                seed_token=seed,
                v1_sp_count=len(members),
                v2_destination_count=len(buckets),
                destinations=[
                    LegacyDestination(
                        path=path,
                        sp_count=len(rows),
                        share=round(len(rows) / len(members), 4),
                        examples=_examples(rows, example_limit),
                    )
                    for path, rows in ordered[:destination_limit]
                ],
                residual_count=len(buckets.get(RESIDUAL_PATH_LABEL, [])),
            )
        )
    return comparisons


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _fill_prioritization(report: DiscoverReport) -> None:
    report.prioritization = [
        PriorityRow(
            family=c.label,
            supplier_products=c.sp_count,
            unmapped=c.unmapped,
            dominant_token_coverage=c.label_cohesion,
            technical_feature_coverage=c.technical_feature_coverage,
            mapped=c.mapped,
            rule_exists=c.rule_exists,
        )
        for c in report.clusters
    ]


def _fill_residual(
    report: DiscoverReport,
    residual: Sequence[SupplierProductRow],
    tokens_by_sp: dict[int, list[str]],
) -> None:
    residual_tokens = _document_frequency(tokens_by_sp[r.id] for r in residual)
    report.residual = ResidualReport(
        sp_count=len(residual),
        top_tokens=_top(residual_tokens, TOP_TOKENS),
        examples=[
            ExampleSP(id=r.id, sku=r.supplier_reference, designation=r.designation)
            for r in residual[:RESIDUAL_EXAMPLES]
        ],
    )


def _discover_stage_b_v1(
    report: DiscoverReport,
    remainder: Sequence[SupplierProductRow],
    *,
    min_cluster_size: int,
    example_limit: int,
) -> int:
    """Étage B V1 — un cluster par token dominant, aucune hiérarchie."""
    clusters, residual, tokens_by_sp = assign_clusters(
        remainder, min_cluster_size=min_cluster_size
    )
    report.clusters = sorted(
        (
            _build_cluster_report(
                seed, members, tokens_by_sp, example_limit=example_limit
            )
            for seed, members in clusters.items()
        ),
        key=lambda c: (-c.unmapped, -c.sp_count, c.seed_token),
    )
    _fill_prioritization(report)
    _fill_residual(report, residual, tokens_by_sp)
    return len(residual)


def _discover_stage_b_v2(
    report: DiscoverReport,
    remainder: Sequence[SupplierProductRow],
    *,
    min_cluster_size: int,
    example_limit: int,
    min_bigram_size: int | None,
    recluster_threshold: int,
    top_actionable: int,
    compare_v1: bool,
) -> int:
    """Étage B V2 — bigrammes prioritaires, hiérarchie, familles actionnables."""
    assignment = assign_clusters_v2(
        remainder,
        min_cluster_size=min_cluster_size,
        min_bigram_size=min_bigram_size,
        recluster_threshold=recluster_threshold,
    )
    report.clusters = build_v2_cluster_reports(assignment, example_limit=example_limit)
    report.min_bigram_size = assignment.min_bigram_size
    report.recluster_threshold = recluster_threshold
    report.low_idf_threshold = assignment.low_idf_threshold
    report.top_actionable = top_actionable
    report.subcluster_count = sum(len(c.children) for c in report.clusters)
    report.generic_cluster_count = sum(
        1 for c in report.clusters if c.generic_cluster
    )
    report.actionable_families = build_actionable_families(
        report.clusters,
        min_cluster_size=min_cluster_size,
        low_idf_threshold=assignment.low_idf_threshold,
        example_limit=example_limit,
    )
    report.token_discrimination = discrimination_ledger(
        assignment.token_frequency,
        assignment.token_df,
        assignment.doc_count,
        TOP_DISCRIMINATION,
    )
    report.bigram_discrimination = discrimination_ledger(
        assignment.bigram_frequency,
        assignment.bigram_df,
        assignment.doc_count,
        TOP_DISCRIMINATION,
    )
    _fill_prioritization(report)
    _fill_residual(report, assignment.residual, assignment.tokens_by_sp)
    if compare_v1:
        report.v1_comparison = compare_legacy_clusters(
            remainder, assignment, min_cluster_size=min_cluster_size
        )
    return len(assignment.residual)


def run_catalog_discover(
    session: Session,
    *,
    supplier_name: str | None = None,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    top_clusters: int = DEFAULT_TOP_CLUSTERS,
    example_limit: int = DEFAULT_EXAMPLE_LIMIT,
    version: int = DEFAULT_DISCOVER_VERSION,
    min_bigram_size: int | None = None,
    recluster_threshold: int = DEFAULT_RECLUSTER_THRESHOLD,
    top_actionable: int = DEFAULT_TOP_ACTIONABLE,
    compare_v1: bool = True,
) -> DiscoverReport:
    """Cartographie complète, lecture seule, d'un catalogue fournisseur.

    ``version`` sélectionne l'étage B : 1 (token dominant, schéma JSON figé) ou
    2 (bigrammes discriminants + hiérarchie, défaut). Passer ``version=1`` pour
    le rapport legacy inchangé.
    """
    if version not in DISCOVER_VERSIONS:
        raise ValueError(f"version de discover inconnue : {version!r}")
    started = time.perf_counter()
    supplier_ids = resolve_supplier_ids(session, supplier_name)
    rows = load_supplier_products(session, supplier_ids)

    report = DiscoverReport(
        version=DISCOVER_VERSIONS[version],
        supplier=supplier_name,
        supplier_ids=list(supplier_ids),
        min_cluster_size=min_cluster_size,
        top_clusters=top_clusters,
        example_limit=example_limit,
        product_id_hash_before=product_id_snapshot_hash(rows),
    )

    stats, assigned = classify_known_categories(session, rows)
    report.known_categories = [stats[code] for code in sorted(stats)]

    remainder = [row for row in rows if row.id not in assigned]
    if version == 2:
        residual_count = _discover_stage_b_v2(
            report,
            remainder,
            min_cluster_size=min_cluster_size,
            example_limit=example_limit,
            min_bigram_size=min_bigram_size,
            recluster_threshold=recluster_threshold,
            top_actionable=top_actionable,
            compare_v1=compare_v1,
        )
    else:
        residual_count = _discover_stage_b_v1(
            report,
            remainder,
            min_cluster_size=min_cluster_size,
            example_limit=example_limit,
        )

    clustered = sum(c.sp_count for c in report.clusters)
    duration = time.perf_counter() - started
    report.coverage = CoverageSummary(
        total=len(rows),
        known_category_sp=len(assigned),
        discovery_cluster_sp=clustered,
        residual=residual_count,
        cluster_count=len(report.clusters),
        duration_ms=int(round(duration * 1000)),
        sp_per_sec=round(len(rows) / duration, 1) if duration > 0 else 0.0,
    )

    # Garde-fou lecture seule : aucun product_id n'a bougé pendant l'analyse.
    after = load_supplier_products(session, supplier_ids)
    report.product_id_hash_after = product_id_snapshot_hash(after)
    if report.product_id_hash_after != report.product_id_hash_before:
        raise RuntimeError(
            "catalog-discover est en lecture seule : product_id modifié"
        )
    return report


# --------------------------------------------------------------------------
# Rapport texte
# --------------------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):5.1f}%" if whole else "    -"


def format_text_report(report: DiscoverReport) -> str:
    """Rapport lisible — mêmes chiffres que la sortie JSON."""
    if report.is_v2:
        return _format_text_report_v2(report)
    return _format_text_report_v1(report)


def _format_text_report_v1(report: DiscoverReport) -> str:
    out: list[str] = []
    add = out.append
    cov = report.coverage

    add("=== CATALOG DISCOVER V1 (lecture seule) ===")
    add(f"Fournisseur        : {report.supplier or '(tous)'} ids={report.supplier_ids}")
    add(f"min-cluster-size   : {report.min_cluster_size}")
    add("")
    add("--- 1. Couverture ---")
    add(f"TOTAL SupplierProduct     : {cov.total}")
    add(
        f"Familles connues (rules)  : {cov.known_category_sp} "
        f"({_pct(cov.known_category_sp, cov.total)})"
    )
    add(
        f"Clusters de découverte    : {cov.discovery_cluster_sp} "
        f"({_pct(cov.discovery_cluster_sp, cov.total)})"
    )
    add(f"Résidu non classé         : {cov.residual} ({_pct(cov.residual, cov.total)})")
    add(f"Nombre de clusters        : {cov.cluster_count}")
    add(f"Durée                     : {cov.duration_ms} ms ({cov.sp_per_sec} SP/s)")
    add("")

    add("--- 2. Familles déjà connues (CategoryRule) ---")
    out.extend(_known_category_lines(report))
    add("")

    add(f"--- 3. Priorisation (top {report.top_clusters} par non mappés) ---")
    header = (
        f"{'famille':<22}{'SP':>7}{'non mappés':>12}{'tok.cov':>9}"
        f"{'feat.cov':>10}{'mappés':>8}{'règle':>7}"
    )
    add(header)
    add("-" * len(header))
    for row in report.prioritization[: report.top_clusters]:
        add(
            f"{row.family[:22]:<22}{row.supplier_products:>7}{row.unmapped:>12}"
            f"{row.dominant_token_coverage:>9.2f}{row.technical_feature_coverage:>10.2f}"
            f"{row.mapped:>8}{str(row.rule_exists):>7}"
        )
    add("")

    add(f"--- 4. Détail des clusters (top {report.top_clusters}) ---")
    for idx, cluster in enumerate(report.clusters[: report.top_clusters], start=1):
        add("")
        add(
            f"[{idx}] {cluster.label} — SP={cluster.sp_count} "
            f"mappés={cluster.mapped} non mappés={cluster.unmapped} "
            f"rule_exists={cluster.rule_exists} "
            f"pmc_candidats={cluster.existing_pmc_candidates}"
        )
        add(
            f"    cohésion label={cluster.label_cohesion:.2f} "
            f"top3={cluster.top3_cohesion:.2f} "
            f"features techniques={cluster.technical_feature_sp} "
            f"({cluster.technical_feature_coverage:.2f})"
        )
        add(
            "    tokens   : "
            + ", ".join(f"{t}({n})" for t, n in cluster.top_tokens)
        )
        add(
            "    bigrammes: "
            + ", ".join(f"{b}({n})" for b, n in cluster.top_bigrams)
        )
        if cluster.features:
            add("    variantes techniques :")
            for feat in cluster.features:
                values = ", ".join(f"{v}({n})" for v, n in feat.top_values)
                add(f"      {feat.key} : {feat.sp_count} SP | {values}")
        else:
            add("    variantes techniques : (aucune primitive ne s'applique)")
        add("    exemples :")
        for ex in cluster.examples:
            add(f"      SP {ex.id} | {ex.sku} | {ex.designation[:120]}")
    add("")

    add("--- 5. Résidu non classé ---")
    add(f"SP : {report.residual.sp_count}")
    add(
        "tokens : "
        + ", ".join(f"{t}({n})" for t, n in report.residual.top_tokens)
    )
    for ex in report.residual.examples:
        add(f"  SP {ex.id} | {ex.sku} | {ex.designation[:120]}")
    add("")
    unchanged = report.product_id_hash_before == report.product_id_hash_after
    add(
        f"--- 6. Snapshot product_id : before={report.product_id_hash_before[:16]} "
        f"after={report.product_id_hash_after[:16]} "
        f"({'inchangé' if unchanged else 'MODIFIÉ'})"
    )
    return "\n".join(out)


def _known_category_lines(report: DiscoverReport) -> list[str]:
    out: list[str] = []
    if not report.known_categories:
        out.append("(aucune CategoryRule enregistrée)")
    for stat in report.known_categories:
        out.append(
            f"{stat.code:<16} classifiés={stat.classified:<6} mappés={stat.mapped:<6} "
            f"non mappés={stat.unmapped:<6} PMC={stat.pmc_candidates}"
        )
        out.append(
            f"{'':<16} EXACT={stat.exact} HIGH={stat.high} REVIEW={stat.review} "
            f"AMBIGUOUS={stat.ambiguous} NO_PMC={stat.no_pmc_product} "
            f"INSUFFICIENT={stat.insufficient_data}"
        )
        if stat.correction_sources:
            sources = " ".join(
                f"{k}={stat.correction_sources[k]}"
                for k in sorted(stat.correction_sources)
            )
            out.append(f"{'':<16} correction_source : {sources}")
    return out


def _cluster_detail_lines(cluster: ClusterReport, *, indent: str) -> list[str]:
    out: list[str] = []
    out.append(
        f"{indent}cohésion label={cluster.label_cohesion:.2f} "
        f"top3={cluster.top3_cohesion:.2f} "
        f"co-tokens={cluster.co_token_count} "
        f"features techniques={cluster.technical_feature_sp} "
        f"({cluster.technical_feature_coverage:.2f})"
    )
    out.append(
        f"{indent}tokens   : " + ", ".join(f"{t}({n})" for t, n in cluster.top_tokens)
    )
    out.append(
        f"{indent}bigrammes: " + ", ".join(f"{b}({n})" for b, n in cluster.top_bigrams)
    )
    if cluster.features:
        out.append(f"{indent}variantes techniques :")
        for feat in cluster.features:
            values = ", ".join(f"{v}({n})" for v, n in feat.top_values)
            out.append(f"{indent}  {feat.key} : {feat.sp_count} SP | {values}")
    else:
        out.append(f"{indent}variantes techniques : (aucune primitive ne s'applique)")
    out.append(f"{indent}exemples :")
    for ex in cluster.examples:
        out.append(f"{indent}  SP {ex.id} | {ex.sku} | {ex.designation[:120]}")
    return out


def _format_text_report_v2(report: DiscoverReport) -> str:
    out: list[str] = []
    add = out.append
    cov = report.coverage
    limit = report.top_clusters

    add("=== CATALOG DISCOVER V2 (lecture seule) ===")
    add(f"Fournisseur        : {report.supplier or '(tous)'} ids={report.supplier_ids}")
    add(
        f"Seuils             : min-cluster={report.min_cluster_size} "
        f"min-bigram={report.min_bigram_size} "
        f"recluster>={report.recluster_threshold}"
    )
    add(
        f"Démotion unigramme : idf < {report.low_idf_threshold:.4f} "
        f"(médiane des IDF des parents unigrammes)"
    )
    add("")
    add("--- 1. Couverture ---")
    add(f"TOTAL SupplierProduct     : {cov.total}")
    add(
        f"Familles connues (rules)  : {cov.known_category_sp} "
        f"({_pct(cov.known_category_sp, cov.total)})"
    )
    add(
        f"Clusters de découverte    : {cov.discovery_cluster_sp} "
        f"({_pct(cov.discovery_cluster_sp, cov.total)})"
    )
    add(f"Résidu non classé         : {cov.residual} ({_pct(cov.residual, cov.total)})")
    add(f"Clusters de tête          : {cov.cluster_count}")
    add(f"Sous-clusters (enfants)   : {report.subcluster_count}")
    add(f"Parents génériques        : {report.generic_cluster_count}")
    add(f"Familles actionnables     : {len(report.actionable_families)}")
    add(f"Durée                     : {cov.duration_ms} ms ({cov.sp_per_sec} SP/s)")
    add("")

    add("--- 2. Familles déjà connues (CategoryRule) ---")
    out.extend(_known_category_lines(report))
    add("")

    add(f"--- 3. TOP {report.top_actionable} familles actionnables ---")
    header = (
        f"{'#':>3} {'famille':<30}{'SP':>7}{'non mappés':>12}{'cohés.':>8}"
        f"{'feat.cov':>10}{'idf':>7}{'graine':>9}"
    )
    add(header)
    add("-" * len(header))
    families = report.actionable_families[: report.top_actionable]
    if not families:
        add("(aucune famille ne passe les critères d'exploitabilité)")
    for rank, fam in enumerate(families, start=1):
        add(
            f"{rank:>3} {fam.path[:30]:<30}{fam.sp_count:>7}{fam.unmapped:>12}"
            f"{fam.label_cohesion:>8.2f}{fam.technical_feature_coverage:>10.2f}"
            f"{fam.discrimination:>7.2f}{fam.seed_kind:>9}"
        )
    add("")
    add(
        f"--- 3b. Exemples des {ACTIONABLE_EXAMPLES_DETAILED} premières familles "
        "actionnables ---"
    )
    for rank, fam in enumerate(families[:ACTIONABLE_EXAMPLES_DETAILED], start=1):
        add("")
        add(
            f"[{rank}] {fam.path} — SP={fam.sp_count} non mappés={fam.unmapped} "
            f"head={fam.head} qualifier={fam.qualifier} "
            f"rule_exists={fam.rule_exists}"
        )
        attributes = (
            ", ".join(f"{k}({n})" for k, n in fam.dominant_attributes) or "(aucune)"
        )
        add(f"    attributs dominants : {attributes}")
        add(
            "    bigrammes dominants : "
            + (", ".join(f"{b}({n})" for b, n in fam.dominant_bigrams) or "(aucun)")
        )
        for ex in fam.examples:
            add(f"      SP {ex.id} | {ex.sku} | {ex.designation[:120]}")
    add("")

    add(f"--- 4. Priorisation des clusters de tête (top {limit} par non mappés) ---")
    header = (
        f"{'famille':<22}{'SP':>7}{'non mappés':>12}{'tok.cov':>9}"
        f"{'feat.cov':>10}{'mappés':>8}{'règle':>7}"
    )
    add(header)
    add("-" * len(header))
    for row in report.prioritization[:limit]:
        add(
            f"{row.family[:22]:<22}{row.supplier_products:>7}{row.unmapped:>12}"
            f"{row.dominant_token_coverage:>9.2f}{row.technical_feature_coverage:>10.2f}"
            f"{row.mapped:>8}{str(row.rule_exists):>7}"
        )
    add("")

    add(f"--- 5. Détail des clusters (top {limit}) ---")
    for idx, cluster in enumerate(report.clusters[:limit], start=1):
        add("")
        add(
            f"[{idx}] {cluster.label} — SP={cluster.sp_count} "
            f"mappés={cluster.mapped} non mappés={cluster.unmapped} "
            f"graine={cluster.seed_kind} head={cluster.head} "
            f"qualifier={cluster.qualifier}"
        )
        add(
            f"    df={cluster.document_frequency} "
            f"idf={cluster.discrimination:.2f} "
            f"generic_cluster={cluster.generic_cluster} "
            f"bigrammes locaux={cluster.distinct_local_bigrams} "
            f"part 1er enfant={cluster.top_child_share:.2f}"
        )
        out.extend(_cluster_detail_lines(cluster, indent="    "))
        if cluster.children:
            add(f"    enfants ({len(cluster.children)}) :")
            for child in cluster.children:
                add(
                    f"      - {child.label} — SP={child.sp_count} "
                    f"non mappés={child.unmapped} "
                    f"head={child.head} qualifier={child.qualifier} "
                    f"generic={child.generic_cluster}"
                )
                out.extend(_cluster_detail_lines(child, indent="        "))
            add(f"      - {CHILD_RESIDUAL_LABEL} — SP={cluster.residual_local_count}")
            for ex in cluster.residual_local_examples:
                add(f"          SP {ex.id} | {ex.sku} | {ex.designation[:120]}")
    add("")

    add(f"--- 6. Discrimination (top {TOP_DISCRIMINATION}) ---")
    header = f"{'terme':<28}{'occurrences':>13}{'documents':>11}{'idf':>8}"
    add(header)
    add("-" * len(header))
    for row in report.token_discrimination:
        add(
            f"{row.term[:28]:<28}{row.frequency:>13}"
            f"{row.document_frequency:>11}{row.idf:>8.2f}"
        )
    add("")
    add(f"--- 6b. Discrimination des bigrammes (top {TOP_DISCRIMINATION}) ---")
    add(header)
    add("-" * len(header))
    for row in report.bigram_discrimination:
        add(
            f"{row.term[:28]:<28}{row.frequency:>13}"
            f"{row.document_frequency:>11}{row.idf:>8.2f}"
        )
    add("")

    add("--- 7. Transformation des clusters V1 en V2 ---")
    if not report.v1_comparison:
        add("(comparaison V1 désactivée)")
    for comparison in report.v1_comparison:
        add("")
        add(
            f"{comparison.label} — V1 SP={comparison.v1_sp_count} "
            f"destinations V2={comparison.v2_destination_count} "
            f"résidu V2={comparison.residual_count}"
        )
        if not comparison.v1_sp_count:
            add("    (aucun cluster V1 sous ce label)")
            continue
        for dest in comparison.destinations:
            add(f"    {dest.path:<40} {dest.sp_count:>6} ({dest.share:.2%})")
            for ex in dest.examples:
                add(f"        SP {ex.id} | {ex.sku} | {ex.designation[:110]}")
    add("")

    add("--- 8. Résidu non classé ---")
    add(f"SP : {report.residual.sp_count}")
    add("tokens : " + ", ".join(f"{t}({n})" for t, n in report.residual.top_tokens))
    for ex in report.residual.examples:
        add(f"  SP {ex.id} | {ex.sku} | {ex.designation[:120]}")
    add("")
    unchanged = report.product_id_hash_before == report.product_id_hash_after
    add(
        f"--- 9. Snapshot product_id : before={report.product_id_hash_before[:16]} "
        f"after={report.product_id_hash_after[:16]} "
        f"({'inchangé' if unchanged else 'MODIFIÉ'})"
    )
    return "\n".join(out)
