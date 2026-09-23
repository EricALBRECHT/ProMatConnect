from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://promatconnect:local_demo_only@localhost:5432/promatconnect"
    )
    user_latitude: float = Field(default=48.8566, ge=-90, le=90)
    user_longitude: float = Field(default=2.3522, ge=-180, le=180)
    seed_on_start: bool = True

    site_address: str = "10 rue du Chantier, 75004 Paris"
    company_address: str = "20 rue de l'Entreprise, 75011 Paris"
    company_latitude: float = Field(default=48.8600, ge=-90, le=90, allow_inf_nan=False)
    company_longitude: float = Field(default=2.3800, ge=-180, le=180, allow_inf_nan=False)
    cost_per_km: Decimal = Field(default=Decimal("0.50"), ge=0, le=1000, decimal_places=2)
    time_value_per_hour: Decimal = Field(default=Decimal("30.00"), ge=0, le=10000, decimal_places=2)
    extra_stop_cost: Decimal = Field(default=Decimal("5.00"), ge=0, le=10000, decimal_places=2)
    optimizer_max_agencies: int = Field(default=8, ge=1, le=10)
