from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

CENT = Decimal("0.01")


class CostParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    cost_per_km: Decimal = Field(default=Decimal("0.50"), ge=0, le=1000, decimal_places=2)
    time_value_per_hour: Decimal = Field(default=Decimal("30.00"), ge=0, le=10000, decimal_places=2)
    extra_stop_cost: Decimal = Field(default=Decimal("5.00"), ge=0, le=10000, decimal_places=2)


class CostBreakdown(BaseModel):
    distance_cost: Decimal
    time_cost: Decimal
    extra_stops_cost: Decimal
    estimated_procurement_cost: Decimal
    total_minutes: Decimal


class ProcurementCostService:
    """Préparation parallèle attendue avant départ, puis trajet complet (sans chevauchement)."""

    def __init__(self, parameters: CostParameters):
        self.parameters = parameters

    def calculate(
        self,
        materials: Decimal,
        distance_km: Decimal,
        travel_minutes: Decimal,
        preparation_minutes: int,
        stops: int,
    ) -> CostBreakdown:
        if (
            materials < 0
            or distance_km < 0
            or travel_minutes < 0
            or preparation_minutes < 0
            or stops < 0
        ):
            raise ValueError("Les composantes du coût doivent être positives ou nulles.")
        time = travel_minutes + Decimal(preparation_minutes)
        distance_cost = (distance_km * self.parameters.cost_per_km).quantize(CENT, ROUND_HALF_UP)
        time_cost = (time * self.parameters.time_value_per_hour / Decimal(60)).quantize(
            CENT, ROUND_HALF_UP
        )
        extra = (Decimal(max(stops - 1, 0)) * self.parameters.extra_stop_cost).quantize(
            CENT, ROUND_HALF_UP
        )
        return CostBreakdown(
            distance_cost=distance_cost,
            time_cost=time_cost,
            extra_stops_cost=extra,
            estimated_procurement_cost=materials + distance_cost + time_cost + extra,
            total_minutes=time,
        )
