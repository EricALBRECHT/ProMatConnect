"""Service mapping produit V1 — PLAQUE_PLATRE uniquement.

Dry-run / persist propositions : ne touche JAMAIS SupplierProduct.product_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
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
from app.services.product_mapping.plaque_extractor import extract_plaque_platre
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


@dataclass
class MappingExample:
    supplier_reference: str
    designation: str
    extracted: dict[str, Any]
    candidate_code: str | None
    status: str
    score: float | None
    existing_product_id: int | None


@dataclass
class MappingRunResult:
    analysed: int = 0
    classified: int = 0
    exact: int = 0
    high: int = 0
    review: int = 0
    unmapped: int = 0
    examples: list[MappingExample] = field(default_factory=list)
    persisted_features: int = 0
    persisted_proposals: int = 0

    def to_dict(self) -> dict:
        return {
            "analysed": self.analysed,
            "classified": self.classified,
            "exact": self.exact,
            "high": self.high,
            "review": self.review,
            "unmapped": self.unmapped,
            "persisted_features": self.persisted_features,
            "persisted_proposals": self.persisted_proposals,
            "examples": [
                {
                    "supplier_reference": e.supplier_reference,
                    "designation": e.designation,
                    "extracted": e.extracted,
                    "candidate": e.candidate_code,
                    "status": e.status,
                    "score": e.score,
                    "existing_product_id": e.existing_product_id,
                }
                for e in self.examples
            ],
        }


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
                Product.code.like("PMC-BA13-%"),
                Product.subcategory == "Plaques de plâtre",
            ),
            Product.is_active.is_(True),
        )
    ).all()
    return [(p.id, p.code, p.attributes) for p in rows]


class ProductMappingService:
    def __init__(self, session: Session):
        self.session = session

    def run_plaque_platre(
        self,
        *,
        supplier_name: str = "BRICO_DEPOT",
        limit: int = 50,
        dry_run: bool = True,
        persist: bool = False,
        example_limit: int = 5,
    ) -> MappingRunResult:
        ensure_plaque_platre_category(self.session)
        candidates = load_plaque_pmc_candidates(self.session)

        suppliers = self.session.scalars(
            select(Supplier).where(
                Supplier.name.in_((supplier_name, "BRICO_DEPOT", "BRICO DEPOT"))
            )
        ).all()
        if not suppliers:
            return MappingRunResult()

        supplier_ids = [s.id for s in suppliers]
        sps = list(
            self.session.scalars(
                select(SupplierProduct)
                .where(
                    SupplierProduct.supplier_id.in_(supplier_ids),
                    or_(
                        SupplierProduct.designation.ilike("%plaque%"),
                        SupplierProduct.designation.ilike("%BA13%"),
                        SupplierProduct.designation.ilike("%plâtre%"),
                        SupplierProduct.designation.ilike("%platre%"),
                        SupplierProduct.designation.ilike("%Purelight%"),
                    ),
                )
                .order_by(SupplierProduct.id)
                .limit(max(limit * 4, 50))
            ).all()
        )

        result = MappingRunResult()
        snapshot_product_ids = {sp.id: sp.product_id for sp in sps}

        for sp in sps:
            if result.classified >= limit:
                break

            extraction = extract_plaque_platre(designation=sp.designation or "")
            result.analysed += 1
            if not extraction.classified:
                continue
            result.classified += 1

            match = best_match(extraction.attributes, candidates)
            if match.status == PROPOSAL_EXACT:
                result.exact += 1
            elif match.status == PROPOSAL_HIGH:
                result.high += 1
            elif match.status == PROPOSAL_REVIEW:
                result.review += 1
            else:
                result.unmapped += 1

            if len(result.examples) < example_limit:
                result.examples.append(
                    MappingExample(
                        supplier_reference=sp.supplier_reference,
                        designation=(sp.designation or "")[:120],
                        extracted=extraction.attributes,
                        candidate_code=match.product_code,
                        status=match.status.upper(),
                        score=float(match.score) if match.score is not None else None,
                        existing_product_id=sp.product_id,
                    )
                )

            if persist and not dry_run:
                self._upsert_feature(sp.id, extraction)
                self._upsert_proposal(sp.id, match)
                result.persisted_features += 1
                result.persisted_proposals += 1

        for sp_id, old_pid in snapshot_product_ids.items():
            sp = self.session.get(SupplierProduct, sp_id)
            if sp is not None and sp.product_id != old_pid:
                raise RuntimeError(
                    "V1 mapping ne doit jamais modifier SupplierProduct.product_id"
                )

        if persist and not dry_run:
            self.session.flush()
        return result

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
                score_breakdown=match.breakdown,
                algorithm_version=match.algorithm_version or ALGORITHM_VERSION_PLAQUE_V1,
            )
        )
