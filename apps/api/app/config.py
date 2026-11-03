from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DeepVocal API"
    deepvocal_mode: str = "mock"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    singing_enabled: bool = False
    model_provider: str = "disabled"
    database_url: str = "postgresql+asyncpg://deepvocal:deepvocal@127.0.0.1:15432/deepvocal"
    corpus_root: str = str(Path(__file__).resolve().parents[3] / "datasets")


@lru_cache
def get_settings() -> Settings:
    return Settings()
