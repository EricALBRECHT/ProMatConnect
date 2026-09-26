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

    # Géocodage : "fake" (tests/démo hors-réseau) | "geopf" (Géoplateforme IGN).
    geocoding_provider: str = Field(default="fake")
    geocoding_timeout_s: float = Field(default=10.0, gt=0, le=60)

    # Cache fournisseurs LIVE partagé (secondes).
    supplier_store_cache_ttl_s: int = Field(default=7 * 24 * 3600, ge=60, le=90 * 24 * 3600)
    supplier_offer_price_ttl_s: int = Field(default=60 * 60, ge=30, le=24 * 3600)
    supplier_offer_stock_ttl_s: int = Field(default=10 * 60, ge=30, le=24 * 3600)
    supplier_cache_refresh_lease_s: int = Field(default=30, ge=5, le=300)

    # Brico Dépôt LIVE (désactivé par défaut — aucun appel réseau).
    bricodepot_live_enabled: bool = Field(default=False)
    bricodepot_max_stores: int = Field(default=3, ge=1, le=5)
