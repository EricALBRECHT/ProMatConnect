"""Service mapping produit V2 — adaptateurs de rapport au-dessus du pipeline.

Toute la mécanique (sélection, extraction, matching, garde-fous) vit dans
pipeline.py + generic_matcher.py, pilotée par les CategoryRule. Ce module ne
fait plus que projeter le résultat générique dans les structures de rapport
attendues par le CLI.

Dry-run / analyse : ne touche JAMAIS SupplierProduct.product_id.
Les mappings manuels (product_id déjà posé) → ALREADY_MAPPED, non recalculés.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.product_mapping import (
    KIND_FOURRURE,
    KIND_MONTANT,
    KIND_RAIL,
    PROPOSAL_UNMAPPED,
    ProductCategory,
)
from app.services.product_mapping import pipeline
from app.services.product_mapping.generic_matcher import (
    REASON_ALREADY_MAPPED,
    REASON_AMBIGUOUS,
    REASON_EXACT,
    REASON_INSUFFICIENT_DATA,
    REASON_NO_PMC_PRODUCT,
    REASON_NOT_THIS_CATEGORY,
    REASON_REVIEW,
    MatchResult,
    best_match,
)
from app.services.product_mapping.pipeline import (
    OUTCOME_ALREADY_MAPPED,
    OUTCOME_NOT_THIS_CATEGORY,
    ApplyExactResult,
    CategoryRunResult,
    ExactApplication,
    resolve_supplier_ids,
)
from app.services.product_mapping.plaque_extractor import (
    diagnose_attribute_gaps,
    extract_plaque_platre,
)
from app.services.product_mapping.rules.ossature_placo import (
    OSSATURE_ATTR_DEFS,
    OSSATURE_PLACO_RULE,
)
from app.services.product_mapping.rules.plaque_platre import (
    PLAQUE_ATTR_DEFS,
    PLAQUE_PLATRE_RULE,
)

__all__ = [
    "ApplyExactResult",
    "ExactApplication",
    "MappingExample",
    "MappingRunResult",
    "OSSATURE_ATTR_DEFS",
    "OssatureRunResult",
    "PLAQUE_ATTR_DEFS",
    "ProductMappingService",
    "ensure_ossature_placo_category",
    "ensure_plaque_platre_category",
    "is_exact_applicable",
    "load_ossature_pmc_candidates",
    "load_plaque_pmc_candidates",
    "resolve_supplier_ids",
    "sql_ossature_candidates",
    "sql_plaque_candidates",
]


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


def is_exact_applicable(
    *,
    sp: Any,
    extraction_attrs: dict[str, Any],
    match: MatchResult,
) -> bool:
    """True si le mapping EXACT PLAQUE peut être appliqué sans UNKNOWN critique."""
    return pipeline.is_exact_applicable(
        PLAQUE_PLATRE_RULE,
        sp=sp,
        extraction_attrs=extraction_attrs,
        match=match,
    )


@dataclass
class MappingRunResult:
    """Résultat d'analyse PLAQUE — compteurs + variantes + manques PMC."""

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
    exact_applications: list[ExactApplication] = field(default_factory=list)
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
            "exact_applications": [
                {
                    "supplier_product_id": a.supplier_product_id,
                    "supplier_reference": a.supplier_reference,
                    "designation": a.designation,
                    "product_id": a.product_id,
                    "product_code": a.product_code,
                    "extracted": a.extracted,
                }
                for a in self.exact_applications
            ],
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


@dataclass
class OssatureRunResult:
    supplier_product_total: int = 0
    initial_candidates: int = 0
    not_this_category: int = 0
    true_elements: int = 0
    rails: int = 0
    montants: int = 0
    fourrures: int = 0
    already_mapped: int = 0
    exact: int = 0
    review: int = 0
    no_pmc_product: int = 0
    insufficient_data: int = 0
    ambiguous: int = 0
    analysed: int = 0
    missing_pmc_by_variant: dict[str, int] = field(default_factory=dict)
    near_length_cases: list[dict[str, Any]] = field(default_factory=list)
    insufficient_cases: list[dict[str, Any]] = field(default_factory=list)
    examples: list[MappingExample] = field(default_factory=list)
    false_positive_samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "supplier_product_total": self.supplier_product_total,
            "initial_candidates": self.initial_candidates,
            "not_this_category": self.not_this_category,
            "true_elements": self.true_elements,
            "rails": self.rails,
            "montants": self.montants,
            "fourrures": self.fourrures,
            "already_mapped": self.already_mapped,
            "exact": self.exact,
            "review": self.review,
            "no_pmc_product": self.no_pmc_product,
            "insufficient_data": self.insufficient_data,
            "ambiguous": self.ambiguous,
            "analysed": self.analysed,
            "missing_pmc_by_variant": self.missing_pmc_by_variant,
            "near_length_cases": self.near_length_cases,
            "insufficient_cases": self.insufficient_cases,
            "false_positive_samples": self.false_positive_samples,
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


# --------------------------------------------------------------------------
# Façades catégorie (compat CLI / tests)
# --------------------------------------------------------------------------

def ensure_plaque_platre_category(session: Session) -> ProductCategory:
    return pipeline.ensure_category(session, PLAQUE_PLATRE_RULE)


def ensure_ossature_placo_category(session: Session) -> ProductCategory:
    return pipeline.ensure_category(session, OSSATURE_PLACO_RULE)


def load_plaque_pmc_candidates(session: Session) -> list[tuple[int, str, dict | None]]:
    return [
        (pid, code, attrs)
        for pid, code, attrs, _sub in pipeline.load_pmc_candidates(
            session, PLAQUE_PLATRE_RULE
        )
    ]


def load_ossature_pmc_candidates(
    session: Session,
) -> list[tuple[int, str, dict | None, str | None]]:
    return pipeline.load_pmc_candidates(session, OSSATURE_PLACO_RULE)


def sql_plaque_candidates(session: Session, supplier_ids: list[int], *, limit=None):
    return pipeline.sql_supplier_candidates(
        session, PLAQUE_PLATRE_RULE, supplier_ids, limit=limit
    )


def sql_ossature_candidates(session: Session, supplier_ids: list[int], *, limit=None):
    return pipeline.sql_supplier_candidates(
        session, OSSATURE_PLACO_RULE, supplier_ids, limit=limit
    )


# --------------------------------------------------------------------------
# Libellés de variantes
# --------------------------------------------------------------------------

def _variant_label(attrs: dict[str, Any]) -> str:
    t = attrs.get("type") or "?"
    return f"{t} {_dims_label(attrs)}"


def _dims_label(attrs: dict[str, Any]) -> str:
    L = attrs.get("length_mm")
    W = attrs.get("width_mm")
    Th = attrs.get("thickness_mm")
    if L is None or W is None or Th is None:
        return "dims?"
    return f"{L}×{W}×{Th}"


def _ossature_variant_label(attrs: dict[str, Any]) -> str:
    # Regroupement NO_PMC sur longueur nominale (identité matching)
    ln = attrs.get("nominal_length_mm")
    if ln is None:
        ln = attrs.get("length_mm")
    return (
        f"{attrs.get('kind') or '?'} "
        f"{attrs.get('profile') or '?'} "
        f"{ln if ln is not None else '?'}"
    )


def _example(item, *, status: str, reason: str, score: float | None) -> MappingExample:
    return MappingExample(
        supplier_reference=item.sp.supplier_reference,
        designation=(item.sp.designation or "")[:160],
        extracted=item.attrs,
        candidate_code=item.match.product_code if item.match else None,
        status=status,
        reason=reason,
        score=score,
        existing_product_id=item.sp.product_id,
    )


class ProductMappingService:
    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------------
    # PLAQUE_PLATRE
    # ------------------------------------------------------------------

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
        run = pipeline.run_category(
            self.session,
            PLAQUE_PLATRE_RULE,
            supplier_name=supplier_name,
            limit=limit,
            all_candidates=all_candidates,
            example_limit=example_limit,
            persist=persist,
            dry_run=dry_run,
        )
        return self._plaque_report(run, example_limit=example_limit)

    def _plaque_report(
        self, run: CategoryRunResult, *, example_limit: int
    ) -> MappingRunResult:
        result = MappingRunResult()
        result.supplier_product_total = run.supplier_product_total
        result.initial_candidates = run.initial_candidates
        result.analysed = run.analysed
        result.not_this_category = run.not_this_category
        result.true_plaques = run.classified
        result.classified = run.classified
        result.already_mapped = run.already_mapped
        result.exact = run.exact
        result.high = run.high
        result.review = run.review
        result.ambiguous = run.ambiguous
        result.no_pmc_product = run.no_pmc_product
        result.insufficient_data = run.insufficient_data
        result.unmapped = run.unmapped
        result.persisted_features = run.persisted_features
        result.persisted_proposals = run.persisted_proposals

        type_c: Counter[str] = Counter()
        thick_c: Counter[str] = Counter()
        len_c: Counter[str] = Counter()
        width_c: Counter[str] = Counter()
        dims_c: Counter[str] = Counter()
        variant_c: Counter[str] = Counter()
        other_type_c: Counter[str] = Counter()
        missing_c: Counter[str] = Counter()
        known_types = {
            "standard",
            "hydrofuge",
            "multifonctions",
            "legere",
            "feu",
            "phonique",
        }

        for item in run.items:
            sp = item.sp
            if item.outcome == OUTCOME_NOT_THIS_CATEGORY:
                if (
                    item.reason == REASON_NOT_THIS_CATEGORY
                    and len(result.examples) < example_limit
                    and self._interesting_not_category(sp.designation or "")
                ):
                    result.examples.append(
                        _example(
                            item,
                            status=PROPOSAL_UNMAPPED.upper(),
                            reason=REASON_NOT_THIS_CATEGORY,
                            score=None,
                        )
                    )
                continue

            attrs = item.attrs
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

            if item.outcome == OUTCOME_ALREADY_MAPPED:
                # Pas de proposal qui recalculerait — mapping préservé
                if len(result.examples) < example_limit:
                    result.examples.append(
                        _example(
                            item,
                            status="ALREADY_MAPPED",
                            reason=REASON_ALREADY_MAPPED,
                            score=None,
                        )
                    )
                continue

            match = item.match
            assert match is not None
            if item.reason == REASON_EXACT and is_exact_applicable(
                sp=sp, extraction_attrs=attrs, match=match
            ):
                result.exact_applications.append(
                    ExactApplication(
                        supplier_product_id=sp.id,
                        supplier_reference=sp.supplier_reference,
                        designation=(sp.designation or "")[:200],
                        product_id=match.product_id,  # type: ignore[arg-type]
                        product_code=match.product_code or "",
                        extracted=dict(attrs),
                    )
                )
            elif item.reason == REASON_NO_PMC_PRODUCT:
                missing_c[_variant_label(attrs)] += 1

            if len(result.examples) < example_limit:
                result.examples.append(
                    _example(
                        item,
                        status=match.status.upper(),
                        reason=item.reason,
                        score=float(match.score) if match.score is not None else None,
                    )
                )

        result.by_type = dict(type_c.most_common())
        result.by_thickness = dict(thick_c.most_common())
        result.by_length = dict(len_c.most_common())
        result.by_width = dict(width_c.most_common())
        result.by_dims = dict(dims_c.most_common())
        result.by_variant = dict(variant_c.most_common())
        result.other_types = dict(other_type_c.most_common())
        result.missing_pmc_by_variant = dict(missing_c.most_common())
        return result

    def apply_exact_plaque_platre(
        self,
        *,
        supplier_name: str = "BRICO_DEPOT",
        dry_run: bool = True,
    ) -> ApplyExactResult:
        """Applique les mappings EXACT PLAQUE éligibles (transactionnel)."""
        return pipeline.apply_exact_for_rule(
            self.session,
            PLAQUE_PLATRE_RULE,
            supplier_name=supplier_name,
            dry_run=dry_run,
        )

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
            match = best_match(PLAQUE_PLATRE_RULE, extraction.attributes, candidates)
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

    # ------------------------------------------------------------------
    # OSSATURE_PLACO
    # ------------------------------------------------------------------

    def run_ossature_placo(
        self,
        *,
        supplier_name: str = "BRICO_DEPOT",
        limit: int | None = 50,
        all_candidates: bool = False,
        example_limit: int = 10,
    ) -> OssatureRunResult:
        """Analyse dry-run OSSATURE_PLACO — jamais d'écriture product_id."""
        run = pipeline.run_category(
            self.session,
            OSSATURE_PLACO_RULE,
            supplier_name=supplier_name,
            limit=limit,
            all_candidates=all_candidates,
            example_limit=example_limit,
            persist=False,
            dry_run=True,
        )
        return self._ossature_report(run, example_limit=example_limit)

    def _ossature_report(
        self, run: CategoryRunResult, *, example_limit: int
    ) -> OssatureRunResult:
        result = OssatureRunResult()
        result.supplier_product_total = run.supplier_product_total
        result.initial_candidates = run.initial_candidates
        result.analysed = run.analysed
        result.not_this_category = run.not_this_category
        result.true_elements = run.classified
        result.already_mapped = run.already_mapped
        result.exact = run.exact
        result.review = run.review
        result.ambiguous = run.ambiguous
        result.no_pmc_product = run.no_pmc_product
        result.insufficient_data = run.insufficient_data

        missing_c: Counter[str] = Counter()

        for item in run.items:
            sp = item.sp
            if item.outcome == OUTCOME_NOT_THIS_CATEGORY:
                if len(result.false_positive_samples) < 15:
                    result.false_positive_samples.append((sp.designation or "")[:120])
                continue

            attrs = item.attrs
            kind = attrs.get("kind")
            if kind == KIND_RAIL:
                result.rails += 1
            elif kind == KIND_MONTANT:
                result.montants += 1
            elif kind == KIND_FOURRURE:
                result.fourrures += 1

            length = attrs.get("length_mm")
            # Rapport : longueurs réelles 2490/2990 (nominales 2500/3000)
            if length in {2490, 2990}:
                result.near_length_cases.append(
                    {
                        "id": sp.id,
                        "supplier_reference": sp.supplier_reference,
                        "designation": sp.designation,
                        "brand": sp.brand,
                        "kind": kind,
                        "profile": attrs.get("profile"),
                        "length_mm": length,
                        "nominal_length_mm": attrs.get("nominal_length_mm"),
                        "pair": "2490/2500" if length == 2490 else "2990/3000",
                        "product_id": sp.product_id,
                        "correction_source": sp.correction_source,
                    }
                )

            if item.outcome == OUTCOME_ALREADY_MAPPED:
                if len(result.examples) < example_limit:
                    result.examples.append(
                        _example(
                            item,
                            status="ALREADY_MAPPED",
                            reason=REASON_ALREADY_MAPPED,
                            score=None,
                        )
                    )
                continue

            match = item.match
            assert match is not None
            if item.reason == REASON_INSUFFICIENT_DATA:
                result.insufficient_cases.append(
                    _ossature_insufficient_case(sp, attrs)
                )
            elif item.reason == REASON_NO_PMC_PRODUCT:
                missing_c[_ossature_variant_label(attrs)] += 1

            if len(result.examples) < example_limit:
                result.examples.append(
                    _example(
                        item,
                        status=match.status.upper(),
                        reason=item.reason,
                        score=float(match.score) if match.score is not None else None,
                    )
                )

        result.missing_pmc_by_variant = dict(missing_c.most_common())
        return result


def _ossature_insufficient_case(sp, attrs: dict[str, Any]) -> dict[str, Any]:
    """Diagnostic A/B : information absente vs présente mais non extraite."""
    missing = [
        key
        for key in ("kind", "profile", "length_mm", "nominal_length_mm")
        if attrs.get(key) is None
    ]
    des = sp.designation or ""
    group = "B_INFORMATION_REELLEMENT_ABSENTE"
    hint = None
    if "profile" in missing:
        if re.search(r"R\s*\d{2}\d{2}|F\s*\d{2}\d{2}|M\s*\d{2}\d{2}", des, re.I):
            group = "A_INFORMATION_PRESENTE_MAIS_NON_EXTRAITE"
            hint = "code compact type R4830/F4518/M4835"
        elif re.search(r"\b45\s*mm\b|\b48\s*mm\b|\b70\s*mm\b", des, re.I):
            group = "A_INFORMATION_PRESENTE_MAIS_NON_EXTRAITE"
            hint = "largeur mm sans lettre de profil (R/M/F)"
    return {
        "id": sp.id,
        "supplier_reference": sp.supplier_reference,
        "designation": des,
        "kind": attrs.get("kind"),
        "profile": attrs.get("profile"),
        "length_mm": attrs.get("length_mm"),
        "nominal_length_mm": attrs.get("nominal_length_mm"),
        "missing": missing,
        "group": group,
        "hint": hint,
    }
