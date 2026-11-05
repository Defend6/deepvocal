"""Optional real Demucs stem extraction through the isolated local inference bridge."""

import asyncio
import base64

import httpx

from app.config import get_settings
from app.worker import PermanentJobError, RetryableJobError


async def separate(data, claim):
    settings = get_settings()
    if not settings.singing_service_url or len(settings.singing_service_token) < 32:
        raise PermanentJobError("VOCAL_SEPARATION_UNAVAILABLE")
    endpoint = settings.singing_service_url.rstrip("/")
    headers = {"Authorization": "Bearer " + settings.singing_service_token}
    bridge_job = str(claim["worker_token"])
    try:
        async with (
            httpx.AsyncClient(timeout=500, trust_env=False) as client,
            client.stream(
                "POST",
                endpoint + "/generate",
                headers=headers,
                json={
                    "job_id": bridge_job,
                    "owner_id": str(claim["owner_id"]),
                    "reference_audio_base64": base64.b64encode(data).decode(),
                    "lyrics": "separation",
                    "style": "vocals",
                    "duration_seconds": 5,
                    "seed": 0,
                    "operation": "separate",
                },
            ) as response,
        ):
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 140000000:
                    raise PermanentJobError("SEPARATION_OUTPUT_TOO_LARGE")
        import json

        result = base64.b64decode(json.loads(chunks)["audio_base64"], validate=True)
        if len(result) > settings.max_audio_size_bytes:
            raise PermanentJobError("SEPARATION_OUTPUT_TOO_LARGE")
        return result
    except asyncio.CancelledError:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            try:
                await client.delete(endpoint + f"/jobs/{bridge_job}", headers=headers)
            except httpx.HTTPError:
                pass
        raise
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise RetryableJobError("VOCAL_SEPARATION_UNAVAILABLE") from exc
