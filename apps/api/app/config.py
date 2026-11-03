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
    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_bucket: str = "deepvocal-private"
    s3_access_key: str = "deepvocal"
    s3_secret_key: str = "change-me-local-only"
    s3_region: str = "us-east-1"
    s3_presign_ttl_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()
