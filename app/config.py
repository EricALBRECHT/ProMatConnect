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
