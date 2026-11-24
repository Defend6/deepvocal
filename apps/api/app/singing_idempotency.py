from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid5

_GENERATED_ASSET_NAMESPACE = "deepvocal:generated-audio:v1"


def bridge_job_and_claim(claim: dict) -> tuple[str, str]:
    """Keep the business effect ID stable while fencing cancellation by worker claim."""
    job_id = str(UUID(str(claim["id"])))
    claim_id = str(UUID(str(claim["worker_token"])))
    return job_id, claim_id


def generation_asset_id(job_id: str) -> str:
    """Return a stable generated-asset UUID so retries reuse the same private object key."""
    return str(uuid5(UUID(str(job_id)), _GENERATED_ASSET_NAMESPACE))


def bridge_request_fingerprint(payload: dict) -> str:
    """Fingerprint the business request, excluding the attempt-specific fencing token."""
    stable = {key: value for key, value in payload.items() if key != "claim_id"}
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ActiveClaimRegistry:
    """Track cancellable inference processes by worker claim, never by reusable job ID."""

    def __init__(self):
        self._processes = {}

    def register(self, job_id: str, claim_id: str, process) -> None:
        self._processes[str(UUID(claim_id))] = (str(UUID(job_id)), process)

    def cancel_target(self, claim_id: str):
        current = self._processes.get(str(UUID(claim_id)))
        return current[1] if current else None

    def for_job(self, job_id: str):
        target = str(UUID(job_id))
        return next(
            (
                process
                for current_job, process in self._processes.values()
                if current_job == target and process.poll() is None
            ),
            None,
        )

    def discard(self, claim_id: str) -> None:
        self._processes.pop(str(UUID(claim_id)), None)
