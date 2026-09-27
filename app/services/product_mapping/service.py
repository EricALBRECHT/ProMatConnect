"""Service mapping produit V1.1 — PLAQUE_PLATRE.

Dry-run / analyse : ne touche JAMAIS SupplierProduct.product_id.
Les mappings manuels (product_id déjà posé) → ALREADY_MAPPED, non recalculés.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Product, Supplier, SupplierProduct
from app.models.product_mapping import (
    ALGORITHM_VERSION_PLAQUE_V1,
    CATEGORY_PLAQUE_PLATRE,
    EXTRACTOR_VERSION_PLAQUE_V1,
    PROPOSAL_EXACT,
    PROPOSAL_HIGH,
    PROPOSAL_REVIEW,
    PROPOSAL_UNMAPPED,
    ProductAttributeDef,
    ProductCategory,
    ProductMappingProposal,
    SupplierProductFeature,
)
from app.services.product_mapping.plaque_extractor import (
    REASON_ALREADY_MAPPED,
    REASON_AMBIGUOUS,
    REASON_EXACT,
    REASON_HIGH,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
    REASON_NOT_THIS_CATEGORY,
    REASON_REVIEW,
    diagnose_attribute_gaps,
    extract_plaque_platre,
)
from app.services.product_mapping.plaque_matcher import best_match


PLAQUE_ATTR_DEFS: list[dict[str, Any]] = [
    {
        "key": "type",
        "data_type": "enum",
        "required": True,
        "match_role": "identity",
        "enum_values": [
            "standard",
            "hydrofuge",
            "multifonctions",
            "legere",
            "feu",
            "phonique",
        ],
    },
    {
        "key": "length_mm",
        "data_type": "int",
        "required": True,
        "unit": "mm",
        "match_role": "identity",
    },
    {
        "key": "width_mm",
        "data_type": "int",
        "required": True,
        "unit": "mm",
        "match_role": "identity",
    },
    {
        "key": "thickness_mm",
        "data_type": "int",
        "required": True,
        "unit": "mm",
        "match_role": "identity",
    },
    {
        "key": "hydrofuge",
        "data_type": "bool",
        "required": False,
        "match_role": "optional",
    },
    {
        "key": "fire_resistant",
        "data_type": "bool",
        "required": False,
        "match_role": "optional",
    },
    {
        "key": "acoustic",
        "data_type": "bool",
        "required": False,
        "match_role": "optional",
    },
]

# Pré-sélection SQL raisonnable (évite de scanner les 27k en Python)
_SQL_CANDIDATE_FILTERS = (
    SupplierProduct.designation.ilike("%plaque%"),
    SupplierProduct.designation.ilike("%BA13%"),
    SupplierProduct.designation.ilike("%BA18%"),
    SupplierProduct.designation.ilike("%BA10%"),
    SupplierProduct.designation.ilike("%BA15%"),
    SupplierProduct.designation.ilike("%BA25%"),
    SupplierProduct.designation.ilike("%plâtre%"),
    SupplierProduct.designation.ilike("%platre%"),
    SupplierProduct.designation.ilike("%Purelight%"),
    SupplierProduct.designation.ilike("%placo%"),
)


@dataclass
class MappingExample:
    supplier_reference: str
    designation: str
    extracted: dict[str, Any]
    candidate_code: str | None
    status: str
    reason: str
    score: float | None
    existing_product_id: int | None


@dataclass
class MappingRunResult:
    """Résultat d'analyse V1.1 — compteurs + variantes + manques PMC."""

    supplier_product_total: int = 0
    initial_candidates: int = 0
    not_this_category: int = 0
    true_plaques: int = 0
    already_mapped: int = 0
    exact: int = 0
    high: int = 0
    review: int = 0
    no_pmc_product: int = 0
    insufficient_data: int = 0
    ambiguous: int = 0
    # Compat V1
    analysed: int = 0
    classified: int = 0
    unmapped: int = 0
    examples: list[MappingExample] = field(default_factory=list)
    persisted_features: int = 0
    persisted_proposals: int = 0
    # Stats variantes (vraies plaques uniquement)
    by_type: dict[str, int] = field(default_factory=dict)
    by_thickness: dict[str, int] = field(default_factory=dict)
    by_length: dict[str, int] = field(default_factory=dict)
    by_width: dict[str, int] = field(default_factory=dict)
    by_dims: dict[str, int] = field(default_factory=dict)
    by_variant: dict[str, int] = field(default_factory=dict)
    flag_hydrofuge: int = 0
    flag_feu: int = 0
    flag_acoustique: int = 0
    flag_legere: int = 0
    flag_multifonctions: int = 0
    flag_standard: int = 0
    other_types: dict[str, int] = field(default_factory=dict)
    # NO_PMC_PRODUCT regroupé par variante
    missing_pmc_by_variant: dict[str, int] = field(default_factory=dict)
    category_path_available: bool = False
    category_path_note: str = (
        "category_path Magento non persisté sur SupplierProduct — "
        "classification sur designation uniquement."
    )

    def to_dict(self) -> dict:
        return {
            "supplier_product_total": self.supplier_product_total,
            "initial_candidates": self.initial_candidates,
            "not_this_category": self.not_this_category,
            "true_plaques": self.true_plaques,
            "already_mapped": self.already_mapped,
            "exact": self.exact,
            "high": self.high,
            "review": self.review,
            "no_pmc_product": self.no_pmc_product,
            "insufficient_data": self.insufficient_data,
            "ambiguous": self.ambiguous,
            "analysed": self.analysed,
            "classified": self.classified,
            "unmapped": self.unmapped,
            "persisted_features": self.persisted_features,
            "persisted_proposals": self.persisted_proposals,
            "variants": {
                "by_type": self.by_type,
                "by_thickness_mm": self.by_thickness,
                "by_length_mm": self.by_length,
                "by_width_mm": self.by_width,
                "by_dims": self.by_dims,
                "by_variant": self.by_variant,
                "flags": {
                    "standard": self.flag_standard,
                    "hydrofuge": self.flag_hydrofuge,
                    "feu": self.flag_feu,
                    "acoustique": self.flag_acoustique,
                    "legere": self.flag_legere,
                    "multifonctions": self.flag_multifonctions,
                },
                "other_types": self.other_types,
            },
            "missing_pmc_by_variant": self.missing_pmc_by_variant,
            "category_path_available": self.category_path_available,
            "category_path_note": self.category_path_note,
            "examples": [
                {
                    "supplier_reference": e.supplier_reference,
                    "designation": e.designation,
                    "extracted": e.extracted,
                    "candidate": e.candidate_code,
                    "status": e.status,
                    "reason": e.reason,
                    "score": e.score,
                    "existing_product_id": e.existing_product_id,
                }
                for e in self.examples
            ],
        }


def _variant_label(attrs: dict[str, Any]) -> str:
    t = attrs.get("type") or "?"
    L = attrs.get("length_mm")
    W = attrs.get("width_mm")
    Th = attrs.get("thickness_mm")
    dims = (
        f"{L}×{W}×{Th}"
        if L is not None and W is not None and Th is not None
        else "dims?"
    )
    return f"{t} {dims}"


def _dims_label(attrs: dict[str, Any]) -> str:
    L = attrs.get("length_mm")
    W = attrs.get("width_mm")
    Th = attrs.get("thickness_mm")
    if L is None or W is None or Th is None:
        return "dims?"
    return f"{L}×{W}×{Th}"


def ensure_plaque_platre_category(session: Session) -> ProductCategory:
    cat = session.scalar(
        select(ProductCategory).where(ProductCategory.code == CATEGORY_PLAQUE_PLATRE)
    )
    if cat is None:
        cat = ProductCategory(
            code=CATEGORY_PLAQUE_PLATRE,
            name="Plaques de plâtre",
            parent_id=None,
            reference_unit_default="pièce",
            schema_version="1",
        )
        session.add(cat)
        session.flush()

    existing_keys = {
        d.key
        for d in session.scalars(
            select(ProductAttributeDef).where(ProductAttributeDef.category_id == cat.id)
        ).all()
    }
    for spec in PLAQUE_ATTR_DEFS:
        if spec["key"] in existing_keys:
            continue
        session.add(
            ProductAttributeDef(
                category_id=cat.id,
                key=spec["key"],
                data_type=spec["data_type"],
                required=bool(spec.get("required")),
                unit=spec.get("unit"),
                enum_values=spec.get("enum_values"),
                match_role=spec.get("match_role", "optional"),
            )
        )
    session.flush()
    return cat


def load_plaque_pmc_candidates(session: Session) -> list[tuple[int, str, dict | None]]:
    rows = session.scalars(
        select(Product).where(
            or_(
                Product.code.like("PMC-BA%"),
                Product.subcategory == "Plaques de plâtre",
            ),
            Product.is_active.is_(True),
        )
    ).all()
    return [(p.id, p.code, p.attributes) for p in rows]


def resolve_supplier_ids(session: Session, supplier_name: str) -> list[int]:
    names = {supplier_name, "BRICO_DEPOT", "BRICO DEPOT", "Brico Dépôt"}
    suppliers = session.scalars(select(Supplier).where(Supplier.name.in_(names))).all()
    return [s.id for s in suppliers]


def sql_plaque_candidates(
    session: Session,
    supplier_ids: list[int],
    *,
    limit: int | None = None,
) -> list[SupplierProduct]:
    q = (
        select(SupplierProduct)
        .where(
            SupplierProduct.supplier_id.in_(supplier_ids),
            or_(*_SQL_CANDIDATE_FILTERS),
        )
        .order_by(SupplierProduct.id)
    )
    if limit is not None:
        q = q.limit(limit)
    return list(session.scalars(q).all())


class ProductMappingService:
    def __init__(self, session: Session):
        self.session = session

    def run_plaque_platre(
        self,
        *,
        supplier_name: str = "BRICO_DEPOT",
        limit: int | None = 50,
        all_candidates: bool = False,
        dry_run: bool = True,
        persist: bool = False,
        example_limit: int = 10,
    ) -> MappingRunResult:
        ensure_plaque_platre_category(self.session)
        candidates = load_plaque_pmc_candidates(self.session)
        result = MappingRunResult()

        supplier_ids = resolve_supplier_ids(self.session, supplier_name)
        if not supplier_ids:
            return result

        result.supplier_product_total = int(
            self.session.scalar(
                select(func.count())
                .select_from(SupplierProduct)
                .where(SupplierProduct.supplier_id.in_(supplier_ids))
            )
            or 0
        )

        # --all : toute la pré-sélection SQL ; sinon plafond large pour atteindre --limit classifiés
        fetch_limit = None if all_candidates else max((limit or 50) * 20, 200)
        sps = sql_plaque_candidates(
            self.session, supplier_ids, limit=fetch_limit
        )
        result.initial_candidates = len(sps)

        type_c: Counter[str] = Counter()
        thick_c: Counter[str] = Counter()
        len_c: Counter[str] = Counter()
        width_c: Counter[str] = Counter()
        dims_c: Counter[str] = Counter()
        variant_c: Counter[str] = Counter()
        other_type_c: Counter[str] = Counter()
        missing_c: Counter[str] = Counter()

        snapshot_product_ids = {sp.id: sp.product_id for sp in sps}
        known_types = {
            "standard",
            "hydrofuge",
            "multifonctions",
            "legere",
            "feu",
            "phonique",
        }

        for sp in sps:
            if not all_candidates and limit is not None and result.true_plaques >= limit:
                break

            extraction = extract_plaque_platre(designation=sp.designation or "")
            result.analysed += 1

            if not extraction.classified:
                result.not_this_category += 1
                if (
                    extraction.reason == REASON_NOT_THIS_CATEGORY
                    and len(result.examples) < example_limit
                    and self._interesting_not_category(sp.designation or "")
                ):
                    result.examples.append(
                        MappingExample(
                            supplier_reference=sp.supplier_reference,
                            designation=(sp.designation or "")[:160],
                            extracted={},
                            candidate_code=None,
                            status=PROPOSAL_UNMAPPED.upper(),
                            reason=REASON_NOT_THIS_CATEGORY,
                            score=None,
                            existing_product_id=sp.product_id,
                        )
                    )
                continue

            result.classified += 1
            result.true_plaques += 1
            attrs = extraction.attributes
            self._accumulate_variant_stats(
                attrs,
                type_c,
                thick_c,
                len_c,
                width_c,
                dims_c,
                variant_c,
                other_type_c,
                known_types,
                result,
            )

            # Mapping déjà posé (manuel ou historique) — intouchable
            if sp.product_id is not None:
                result.already_mapped += 1
                if len(result.examples) < example_limit:
                    result.examples.append(
                        MappingExample(
                            supplier_reference=sp.supplier_reference,
                            designation=(sp.designation or "")[:160],
                            extracted=attrs,
                            candidate_code=None,
                            status="ALREADY_MAPPED",
                            reason=REASON_ALREADY_MAPPED,
                            score=None,
                            existing_product_id=sp.product_id,
                        )
                    )
                # Pas de persist proposal qui recalculerait — mapping préservé
                continue

            match = best_match(attrs, candidates)
            reason = match.reason or ""
            if not reason:
                if match.status == PROPOSAL_EXACT:
                    reason = REASON_EXACT
                elif match.status == PROPOSAL_HIGH:
                    reason = REASON_HIGH
                elif match.status == PROPOSAL_REVIEW:
                    reason = REASON_REVIEW
                else:
                    reason = REASON_NO_PMC_PRODUCT

            if reason == REASON_EXACT:
                result.exact += 1
            elif reason == REASON_HIGH:
                result.high += 1
            elif reason == REASON_AMBIGUOUS:
                result.ambiguous += 1
            elif reason == REASON_REVIEW:
                result.review += 1
            elif reason == REASON_INSUFFICIENT_DATA:
                result.insufficient_data += 1
                result.unmapped += 1
            elif reason == REASON_NO_PMC_PRODUCT:
                result.no_pmc_product += 1
                result.unmapped += 1
                missing_c[_variant_label(attrs)] += 1
            else:
                result.unmapped += 1
                result.no_pmc_product += 1
                missing_c[_variant_label(attrs)] += 1
                reason = REASON_NO_PMC_PRODUCT

            if len(result.examples) < example_limit:
                result.examples.append(
                    MappingExample(
                        supplier_reference=sp.supplier_reference,
                        designation=(sp.designation or "")[:160],
                        extracted=attrs,
                        candidate_code=match.product_code,
                        status=match.status.upper(),
                        reason=reason,
                        score=float(match.score) if match.score is not None else None,
                        existing_product_id=sp.product_id,
                    )
                )

            if persist and not dry_run:
                self._upsert_feature(sp.id, extraction)
                self._upsert_proposal(sp.id, match)
                result.persisted_features += 1
                result.persisted_proposals += 1

        result.by_type = dict(type_c.most_common())
        result.by_thickness = dict(thick_c.most_common())
        result.by_length = dict(len_c.most_common())
        result.by_width = dict(width_c.most_common())
        result.by_dims = dict(dims_c.most_common())
        result.by_variant = dict(variant_c.most_common())
        result.other_types = dict(other_type_c.most_common())
        result.missing_pmc_by_variant = dict(missing_c.most_common())

        for sp_id, old_pid in snapshot_product_ids.items():
            sp = self.session.get(SupplierProduct, sp_id)
            if sp is not None and sp.product_id != old_pid:
                raise RuntimeError(
                    "V1 mapping ne doit jamais modifier SupplierProduct.product_id"
                )

        if persist and not dry_run:
            self.session.flush()
        return result

    @staticmethod
    def _interesting_not_category(designation: str) -> bool:
        low = designation.lower()
        return any(
            k in low
            for k in (
                "vis",
                "seau",
                "enduit",
                "bande",
                "cheville",
                "porte-plaque",
                "crochet",
                "doublage",
                "sous-couche",
                "batibox",
            )
        )

    def diagnose_unresolved(
        self,
        *,
        supplier_name: str = "BRICO_DEPOT",
    ) -> dict[str, Any]:
        """Diagnostic REVIEW / INSUFFICIENT / NO_PMC — raisons explicites par SP."""
        candidates = load_plaque_pmc_candidates(self.session)
        supplier_ids = resolve_supplier_ids(self.session, supplier_name)
        if not supplier_ids:
            return {"cases": [], "gap_counts": {}, "population": {}}

        sps = sql_plaque_candidates(self.session, supplier_ids, limit=None)
        cases: list[dict[str, Any]] = []
        gap_counter: Counter[str] = Counter()

        for sp in sps:
            extraction = extract_plaque_platre(designation=sp.designation or "")
            if not extraction.classified:
                continue
            if sp.product_id is not None:
                continue
            match = best_match(extraction.attributes, candidates)
            reason = match.reason or ""
            if reason not in {
                REASON_REVIEW,
                REASON_INSUFFICIENT_DATA,
                REASON_AMBIGUOUS,
                REASON_NO_PMC_PRODUCT,
            }:
                continue
            attrs = extraction.attributes
            gaps = diagnose_attribute_gaps(attrs, match_reason=reason)
            for g in gaps:
                gap_counter[g] += 1
            same_dims = [
                {"code": code, "attributes": pattrs}
                for _pid, code, pattrs in candidates
                if pattrs
                and all(
                    attrs.get(k) is not None and pattrs.get(k) == attrs.get(k)
                    for k in ("length_mm", "width_mm", "thickness_mm")
                )
            ][:5]
            cases.append(
                {
                    "id": sp.id,
                    "supplier_reference": sp.supplier_reference,
                    "designation": sp.designation,
                    "product_id": sp.product_id,
                    "classified": True,
                    "type": attrs.get("type"),
                    "length_mm": attrs.get("length_mm"),
                    "width_mm": attrs.get("width_mm"),
                    "thickness_mm": attrs.get("thickness_mm"),
                    "hydrofuge": attrs.get("hydrofuge"),
                    "acoustic": attrs.get("acoustic"),
                    "fire_resistant": attrs.get("fire_resistant"),
                    "match_reason": reason,
                    "match_status": match.status,
                    "candidate": match.product_code,
                    "gaps": gaps,
                    "pmc_same_dims": same_dims,
                }
            )

        return {
            "cases": cases,
            "gap_counts": dict(gap_counter.most_common()),
            "population": {
                "review": sum(1 for c in cases if c["match_reason"] == REASON_REVIEW),
                "insufficient_data": sum(
                    1 for c in cases if c["match_reason"] == REASON_INSUFFICIENT_DATA
                ),
                "no_pmc_product": sum(
                    1 for c in cases if c["match_reason"] == REASON_NO_PMC_PRODUCT
                ),
                "ambiguous": sum(
                    1 for c in cases if c["match_reason"] == REASON_AMBIGUOUS
                ),
                "total": len(cases),
            },
        }

    @staticmethod
    def _accumulate_variant_stats(
        attrs: dict[str, Any],
        type_c: Counter,
        thick_c: Counter,
        len_c: Counter,
        width_c: Counter,
        dims_c: Counter,
        variant_c: Counter,
        other_type_c: Counter,
        known_types: set[str],
        result: MappingRunResult,
    ) -> None:
        t = attrs.get("type")
        type_key = str(t) if t is not None else "unknown"
        type_c[type_key] += 1
        if t == "standard":
            result.flag_standard += 1
        elif t == "hydrofuge":
            result.flag_hydrofuge += 1
        elif t == "feu":
            result.flag_feu += 1
        elif t == "phonique":
            result.flag_acoustique += 1
        elif t == "legere":
            result.flag_legere += 1
        elif t == "multifonctions":
            result.flag_multifonctions += 1
        elif t is not None and t not in known_types:
            other_type_c[str(t)] += 1

        if attrs.get("hydrofuge") is True and t != "hydrofuge":
            result.flag_hydrofuge += 1
        if attrs.get("fire_resistant") is True and t != "feu":
            result.flag_feu += 1
        if attrs.get("acoustic") is True and t != "phonique":
            result.flag_acoustique += 1

        if attrs.get("thickness_mm") is not None:
            thick_c[str(attrs["thickness_mm"])] += 1
        if attrs.get("length_mm") is not None:
            len_c[str(attrs["length_mm"])] += 1
        if attrs.get("width_mm") is not None:
            width_c[str(attrs["width_mm"])] += 1
        dims_c[_dims_label(attrs)] += 1
        variant_c[_variant_label(attrs)] += 1

    def _upsert_feature(self, sp_id: int, extraction) -> None:
        row = self.session.scalar(
            select(SupplierProductFeature).where(
                SupplierProductFeature.supplier_product_id == sp_id
            )
        )
        conf = (
            Decimal(str(extraction.confidence))
            if extraction.confidence is not None
            else None
        )
        if row is None:
            self.session.add(
                SupplierProductFeature(
                    supplier_product_id=sp_id,
                    category_code=extraction.category_code or CATEGORY_PLAQUE_PLATRE,
                    attributes=extraction.attributes,
                    extractor_version=extraction.extractor_version
                    or EXTRACTOR_VERSION_PLAQUE_V1,
                    confidence=conf,
                )
            )
        else:
            row.category_code = extraction.category_code or CATEGORY_PLAQUE_PLATRE
            row.attributes = extraction.attributes
            row.extractor_version = extraction.extractor_version
            row.confidence = conf

    def _upsert_proposal(self, sp_id: int, match) -> None:
        self.session.add(
            ProductMappingProposal(
                supplier_product_id=sp_id,
                product_id=match.product_id,
                status=match.status,
                score=Decimal(str(match.score)) if match.score is not None else None,
                score_breakdown={
                    **(match.breakdown or {}),
                    "reason": match.reason,
                },
                algorithm_version=match.algorithm_version or ALGORITHM_VERSION_PLAQUE_V1,
            )
        )
