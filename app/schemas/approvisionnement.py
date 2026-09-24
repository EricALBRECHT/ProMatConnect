from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.schemas.comparison import ProcurementStrategy
from app.schemas.location import ResolvedOrigin
from app.services.procurement_cost import CostParameters

STRATEGY_KEYS = frozenset({"single_stop", "minimum_materials", "best_compromise"})


class ApprovisionnementWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updated_at: AwareDatetime
    needs_fingerprint: str = Field(min_length=0, max_length=4000)
    strategy: ProcurementStrategy
    origin: ResolvedOrigin | None = None
    cost_parameters: CostParameters | None = None
    currency: str = Field(default="EUR", max_length=3)
    tax_basis: str = Field(default="HT", max_length=8)

    @field_validator("strategy")
    @classmethod
    def strategy_must_be_valid_choice(cls, value: ProcurementStrategy) -> ProcurementStrategy:
        if value.key not in STRATEGY_KEYS:
            raise ValueError("Stratégie inconnue.")
        if not value.valid:
            raise ValueError("Seule une solution valide peut être retenue.")
        return value


class ApprovisionnementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    chantier_id: int
    strategy_key: str
    strategy_title: str
    chosen_at: datetime
    needs_fingerprint: str
    obsolete: bool
    currency: str
    tax_basis: str
    material_total: Decimal | None
    estimated_procurement_cost: Decimal | None
    total_distance_km: Decimal | None
    travel_minutes: Decimal | None
    snapshot: dict
    chantier_updated_at: datetime
