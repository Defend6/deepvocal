from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class S3Client(Protocol):
    def head_bucket(self, *, Bucket: str) -> Any: ...

    def create_bucket(self, *, Bucket: str) -> Any: ...

    def generate_presigned_url(
        self, ClientMethod: str, *, Params: dict[str, Any], ExpiresIn: int
    ) -> str: ...

    def head_object(
        self, *, Bucket: str, Key: str, ChecksumMode: str = "ENABLED"
    ) -> dict[str, Any]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> Any: ...


@dataclass(frozen=True)
class S3StorageConfig:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"
    presign_ttl_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.bucket or "/" in self.bucket:
            raise ValueError("S3 bucket must be a non-empty bucket name")
        if not 60 <= self.presign_ttl_seconds <= 3600:
            raise ValueError("presign TTL must be between 60 and 3600 seconds")


class S3Storage:
    """Small S3-compatible adapter; business authorization stays in the service layer."""

    def __init__(self, config: S3StorageConfig, client: S3Client) -> None:
        self.config = config
        self.client = client

    @classmethod
    def from_settings(cls, settings: Any) -> S3Storage:
        import boto3

        config = S3StorageConfig(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            presign_ttl_seconds=settings.s3_presign_ttl_seconds,
        )
        client = boto3.client(
            "s3",
            endpoint_url=config.endpoint,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name=config.region,
        )
        return cls(config, client)

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.config.bucket)
        except Exception as exc:
            error_code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.config.bucket)

    def create_upload_url(self, object_key: str, content_type: str) -> str:
        self._validate_key(object_key)
        if not content_type.startswith("audio/"):
            raise ValueError("only audio content types can be uploaded")
        params: dict[str, Any] = {
            "Bucket": self.config.bucket,
            "Key": object_key,
            "ContentType": content_type,
            "ServerSideEncryption": "AES256",
        }
        return self.client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=self.config.presign_ttl_seconds,
        )

    def inspect_object(self, object_key: str) -> dict[str, Any]:
        self._validate_key(object_key)
        return self.client.head_object(
            Bucket=self.config.bucket, Key=object_key, ChecksumMode="ENABLED"
        )

    def delete_object(self, object_key: str) -> None:
        self._validate_key(object_key)
        self.client.delete_object(Bucket=self.config.bucket, Key=object_key)

    @staticmethod
    def _validate_key(object_key: str) -> None:
        if not object_key or object_key.startswith("/") or ".." in object_key.split("/"):
            raise ValueError("object key must be a non-empty relative key")
        if len(object_key) > 1024:
            raise ValueError("object key is too long")
