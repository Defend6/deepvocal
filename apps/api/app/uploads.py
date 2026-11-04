from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas import UploadCreateRequest
from app.storage.s3 import S3Storage


class UploadError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class UploadService:
    def __init__(self, session: AsyncSession, storage: S3Storage, settings: Settings) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings

    async def create(self, owner_id: str, request: UploadCreateRequest) -> dict[str, Any]:
        if request.size_bytes > self.settings.max_audio_size_bytes:
            raise UploadError(413, "audio file exceeds configured size limit")
        asset_id = str(uuid.uuid4())
        object_key = f"users/{owner_id}/assets/{asset_id}"
        expires_at = datetime.now(UTC) + timedelta(seconds=self.settings.upload_expiry_seconds)
        upload_url = self.storage.create_upload_url(object_key, request.content_type)
        await self.session.execute(
            text("""
                INSERT INTO audio_assets
                    (id, owner_id, object_key, sha256, content_type, size_bytes, purpose,
                     status, upload_expires_at)
                VALUES
                    (:id, :owner_id, :object_key, :sha256, :content_type, :size_bytes,
                     :purpose, 'pending', :expires_at)
            """),
            {
                "id": asset_id,
                "owner_id": owner_id,
                "object_key": object_key,
                "sha256": request.sha256,
                "content_type": request.content_type,
                "size_bytes": request.size_bytes,
                "purpose": request.purpose,
                "expires_at": expires_at,
            },
        )
        await self.session.commit()
        return {
            "asset_id": asset_id,
            "object_key": object_key,
            "upload_url": upload_url,
            "required_headers": {
                "Content-Type": request.content_type,
                "x-amz-server-side-encryption": "AES256",
            },
            "expires_in": self.settings.s3_presign_ttl_seconds,
        }
