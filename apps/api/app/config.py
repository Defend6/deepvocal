from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DeepVocal API"
    deepvocal_mode: str = "mock"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    singing_enabled: bool = False
    model_provider: str = "disabled"
    auth_mode: str = "local"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    auth_clock_skew_seconds: int = 30
    max_audio_size_bytes: int = 104857600
    upload_expiry_seconds: int = 3600
    database_url: str = "postgresql+asyncpg://deepvocal:deepvocal@127.0.0.1:15432/deepvocal"
    corpus_root: str = str(Path(__file__).resolve().parents[3] / "datasets")
    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_bucket: str = "deepvocal-private"
    s3_access_key: str = "deepvocal"
    s3_secret_key: str = "change-me-local-only"
    s3_region: str = "us-east-1"
    s3_presign_ttl_seconds: int = 900

    @model_validator(mode="after")
    def validate_auth_configuration(self) -> "Settings":
        if self.auth_mode != "local":
            raise ValueError("only local test identity is implemented")
        if self.deepvocal_mode.lower() not in {
            "mock",
            "development",
            "test",
        }:
            raise ValueError("local authentication is only allowed in mock/development/test mode")
        if not 60 <= self.upload_expiry_seconds <= 86400:
            raise ValueError("upload expiry must be between 60 and 86400 seconds")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
