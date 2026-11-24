from uuid import uuid4

from app.singing_idempotency import (
    ActiveClaimRegistry,
    bridge_job_and_claim,
    bridge_request_fingerprint,
    generation_asset_id,
)


class Process:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 0


def test_retry_keeps_business_job_fingerprint_and_generated_asset_stable():
    job_id = uuid4()
    first_claim = {"id": str(job_id), "worker_token": str(uuid4())}
    retry_claim = {"id": str(job_id), "worker_token": str(uuid4())}

    first_job, first_token = bridge_job_and_claim(first_claim)
    retry_job, retry_token = bridge_job_and_claim(retry_claim)

    assert first_job == retry_job == str(job_id)
    assert first_token != retry_token
    assert generation_asset_id(first_job) == generation_asset_id(retry_job)

    base = {
        "job_id": first_job,
        "claim_id": first_token,
        "owner_id": str(uuid4()),
        "reference_audio_base64": "same-input",
        "lyrics": "same lyrics",
        "seed": 12,
    }
    retried = {**base, "claim_id": retry_token}
    assert bridge_request_fingerprint(base) == bridge_request_fingerprint(retried)
    assert bridge_request_fingerprint({**retried, "lyrics": "different"}) != bridge_request_fingerprint(base)


def test_cancel_claim_targets_only_the_matching_attempt():
    job_id = str(uuid4())
    old_claim, current_claim = str(uuid4()), str(uuid4())
    old_process, current_process = Process(), Process()
    registry = ActiveClaimRegistry()
    registry.register(job_id, old_claim, old_process)
    registry.register(job_id, current_claim, current_process)

    assert registry.cancel_target(old_claim) is old_process
    assert registry.cancel_target(current_claim) is current_process
    assert registry.for_job(job_id) is old_process
    old_process.terminated = True
    assert registry.for_job(job_id) is current_process
    registry.discard(old_claim)
    assert registry.cancel_target(old_claim) is None
    assert registry.cancel_target(current_claim) is current_process
    assert registry.for_job(job_id) is current_process
