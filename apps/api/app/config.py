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
    singing_service_url: str = ""
    singing_service_token: str = ""
    model_provider: str = "disabled"
    auth_mode: str = "local"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = "deepvocal-web"
    request_limit_per_minute: int = 120
    jobs_daily_limit: int = 100
    jobs_concurrent_limit: int = 5
    operator_role: str = "deepvocal-admin"
    metrics_token: str = ""
    upload_completion_enabled: bool = True
    auth_clock_skew_seconds: int = 30
    max_audio_size_bytes: int = 104857600
    upload_expiry_seconds: int = 3600
    database_url: str = "postgresql+asyncpg://deepvocal:deepvocal@127.0.0.1:15432/deepvocal"
    redis_url: str = "redis://127.0.0.1:16379/0"
    model_api_base: str = "https://api.siliconflow.cn/v1"
    model_api_key: str = ""
    model_budget_enforced: bool = True
    # Optional local endpoints keep embeddings and reranking independent from chat APIs.
    embedding_api_base: str = ""
    reranker_api_base: str = ""
    chat_model: str = "Qwen/Qwen3-8B"
    embedding_model: str = "BAAI/bge-m3"
    embedding_model_revision: str = ""
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_model_revision: str = ""
    model_monthly_limit_minor: int = 0
    model_prices_json: str = "{}"
    model_billing_verified: bool = False
    audio_process_timeout_seconds: int = 90
    audio_process_memory_mib: int = 1536
    audio_runtime_root: str = str(Path(__file__).resolve().parents[3] / "runtime" / "audio")
    knowledge_mode: str = "research"
    knowledge_bundle: str = str(
        Path(__file__).resolve().parents[3] / "datasets" / "working" / "knowledge.json"
    )
    knowledge_index_manifest: str = str(
        Path(__file__).resolve().parents[3] / "datasets" / "indexes" / "active.json"
    )
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change-me-local-only"
    milvus_uri: str = "http://127.0.0.1:19530"
    corpus_root: str = str(Path(__file__).resolve().parents[3] / "datasets")
    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_public_endpoint: str = ""
    s3_bucket: str = "deepvocal-private"
    s3_access_key: str = "deepvocal"
    s3_secret_key: str = "change-me-local-only"
    s3_region: str = "us-east-1"
    s3_presign_ttl_seconds: int = 900

    @model_validator(mode="after")
    def validate_auth_configuration(self) -> "Settings":
        if self.auth_mode not in {"local", "oidc"}:
            raise ValueError("auth_mode must be local or oidc")
        if self.auth_mode == "local" and self.deepvocal_mode.lower() not in {
            "mock",
            "development",
            "test",
        }:
            raise ValueError("local authentication is only allowed in mock/development/test mode")
        if self.auth_mode == "oidc":
            from urllib.parse import urlsplit

            if not self.oidc_audience or not self.oidc_client_id:
                raise ValueError("OIDC audience and client id required")
            for value in (self.oidc_issuer, self.oidc_jwks_url):
                url = urlsplit(value)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username:
                    raise ValueError("OIDC issuer and JWKS URLs required")
                if self.deepvocal_mode == "production" and url.scheme != "https":
                    raise ValueError("production OIDC URLs must use HTTPS")
        if not 10 <= self.request_limit_per_minute <= 10000:
            raise ValueError("invalid request rate limit")
        if not 1 <= self.jobs_daily_limit <= 100000 or not 1 <= self.jobs_concurrent_limit <= 100:
            raise ValueError("invalid job quota")
        if not 60 <= self.upload_expiry_seconds <= 86400:
            raise ValueError("upload expiry must be between 60 and 86400 seconds")
        if not 0 <= self.model_monthly_limit_minor <= 100000000:
            raise ValueError("invalid model budget")
        if not 1 <= self.audio_process_timeout_seconds <= 300:
            raise ValueError("invalid audio process timeout")
        if not 512 <= self.audio_process_memory_mib <= 4096:
            raise ValueError("invalid audio process memory limit")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
