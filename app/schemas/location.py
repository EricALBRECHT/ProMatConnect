from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

OriginType = Literal["site", "current_location", "company", "other"]


class Coordinates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class OriginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    type: OriginType = "site"
    address: str | None = Field(default=None, min_length=3, max_length=300)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def coherent_location(self):
        has_coordinates = self.latitude is not None and self.longitude is not None
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Fournissez ensemble latitude et longitude.")
        if self.address is not None and has_coordinates:
            raise ValueError("Fournissez une adresse ou des coordonnées, pas les deux.")
        if self.type == "current_location" and (not has_coordinates or self.address is not None):
            raise ValueError("Ma position nécessite les coordonnées autorisées du navigateur.")
        if self.type == "company" and (has_coordinates or self.address is not None):
            raise ValueError("L'adresse entreprise provient de la configuration.")
        if self.type in ("site", "other") and not (has_coordinates or self.address):
            raise ValueError("Saisissez une adresse ou des coordonnées.")
        return self


class ResolvedOrigin(Coordinates):
    type: OriginType = "site"
    label: str
    address: str | None = None
    source: str


class RoutePoint(Coordinates):
    key: str
    label: str
    agency_id: int | None = None


class RouteLeg(BaseModel):
    start: RoutePoint
    end: RoutePoint
    distance_km: float = Field(ge=0, allow_inf_nan=False)
    duration_minutes: float = Field(ge=0, allow_inf_nan=False)


class RouteResult(BaseModel):
    points: list[RoutePoint]
    legs: list[RouteLeg]
    total_distance_km: float
    travel_minutes: float
    provider: str
    simulated: bool
    order_method: str
