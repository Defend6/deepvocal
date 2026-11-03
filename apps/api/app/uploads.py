from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas import UploadCreateRequest
from app.storage.s3 import S3Storage

logger = logging.getLogger(__name__)


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
        upload_url = self.storage.create_upload_url(
            object_key, request.content_type, request.sha256
        )
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
        headers = {
            "Content-Type": request.content_type,
            "x-amz-checksum-sha256": self.storage.expected_checksum_b64(request.sha256),
            "x-amz-server-side-encryption": "AES256",
            "x-amz-meta-sha256": request.sha256,
        }
        return {
            "asset_id": asset_id,
            "object_key": object_key,
            "upload_url": upload_url,
            "required_headers": headers,
            "expires_in": self.settings.s3_presign_ttl_seconds,
        }

    async def complete(self, owner_id: str, asset_id: str) -> dict[str, Any]:
        result = await self.session.execute(
            text("""
                SELECT id, object_key, sha256, content_type, size_bytes, status,
                       upload_expires_at, completed_at
                FROM audio_assets
                WHERE id = :id AND owner_id = :owner_id
                FOR UPDATE
            """),
            {"id": asset_id, "owner_id": owner_id},
        )
        asset = result.mappings().first()
        if asset is None:
            raise UploadError(404, "audio asset not found")
        if asset["status"] == "ready":
            await self.session.commit()
            return self._ready_response(asset)
        if asset["status"] != "pending":
            raise UploadError(409, "audio asset is not awaiting upload")
        if asset["upload_expires_at"] <= datetime.now(UTC):
            await self._invalidate(asset["id"], asset["object_key"])
            raise UploadError(410, "upload has expired")

        try:
            head = self.storage.inspect_object(asset["object_key"])
            self._validate_head(asset, head)
        except UploadError:
            await self._invalidate(asset["id"], asset["object_key"])
            raise
        except Exception as exc:
            await self.session.rollback()
            raise UploadError(422, "uploaded object is missing or could not be verified") from exc

        completed_at = datetime.now(UTC)
        await self.session.execute(
            text("""
                UPDATE audio_assets
                SET status = 'ready', completed_at = :completed_at
                WHERE id = :id AND owner_id = :owner_id AND status = 'pending'
            """),
            {"id": asset_id, "owner_id": owner_id, "completed_at": completed_at},
        )
        await self.session.commit()
        return {
            "asset_id": asset_id,
            "status": "ready",
            "content_type": asset["content_type"],
            "size_bytes": asset["size_bytes"],
            "sha256": asset["sha256"],
            "completed_at": completed_at,
        }

    def _validate_head(self, asset: Any, head: dict[str, Any]) -> None:
        expected_checksum = self.storage.expected_checksum_b64(asset["sha256"])
        metadata = {key.lower(): value for key, value in head.get("Metadata", {}).items()}
        if head.get("ContentLength") != asset["size_bytes"]:
            raise UploadError(422, "uploaded object size does not match the declaration")
        if head.get("ContentType") != asset["content_type"]:
            raise UploadError(422, "uploaded object content type does not match the declaration")
        if head.get("ChecksumSHA256") != expected_checksum:
            raise UploadError(422, "uploaded object SHA-256 checksum does not match")
        if metadata.get("sha256") != asset["sha256"]:
            raise UploadError(422, "uploaded object SHA-256 metadata does not match")

    async def _invalidate(self, asset_id: str, object_key: str) -> None:
        try:
            self.storage.delete_object(object_key)
        except Exception:
            # The invalid state prevents use; an operational cleanup job can retry deletion.
            logger.exception("Failed to remove invalid uploaded object")
        await self.session.execute(
            text("UPDATE audio_assets SET status = 'invalid' WHERE id = :id"), {"id": asset_id}
        )
        await self.session.commit()

    @staticmethod
    def _ready_response(asset: Any) -> dict[str, Any]:
        return {
            "asset_id": asset["id"],
            "status": "ready",
            "content_type": asset["content_type"],
            "size_bytes": asset["size_bytes"],
            "sha256": asset["sha256"],
            "completed_at": asset["completed_at"],
        }
