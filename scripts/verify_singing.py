"""Explicit local-only real inference probe using an original synthetic reference tone."""
import base64
import argparse
import io
import json
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
import soundfile as sf
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--operation", choices=("generate", "separate"), default="generate")
operation = parser.parse_args().operation
config = dotenv_values(root / ".env")
owner, job = str(uuid4()), str(uuid4())
buffer = io.BytesIO()
clock = np.arange(16000 * 5) / 16000
sf.write(buffer, .2 * np.sin(2 * np.pi * 220 * clock), 16000, format="WAV")
headers = {"Authorization": "Bearer " + config["SINGING_SERVICE_TOKEN"]}
with httpx.Client(timeout=1800, trust_env=False) as client:
    response = client.post("http://127.0.0.1:8090/generate", headers=headers, json={
        "job_id": job, "owner_id": owner,
        "operation": operation,
        "reference_audio_base64": base64.b64encode(buffer.getvalue()).decode(),
        "lyrics": "[verse]\n轻轻唱出心中的旋律", "style": "Chinese solo vocal, clean singing",
        "duration_seconds": 5, "seed": 42})
    if response.status_code != 200:
        raise SystemExit(f"Real inference failed with HTTP {response.status_code}; inspect local inference log")
    result = response.json()
    audio = base64.b64decode(result["audio_base64"], validate=True)
    samples, rate = sf.read(io.BytesIO(audio))
    assert len(samples) > 0 and np.isfinite(samples).all()
    assert 4 <= len(samples) / rate <= 10
    destination = root / f"_qa/{operation}-output.wav"
    destination.write_bytes(audio)
    (root / f"_qa/{operation}-verification.json").write_text(json.dumps({
        "engine": result["engine"], "duration_seconds": len(samples) / rate,
        "sample_rate": rate, "reference": "original synthetic tone; not a voice identity quality test",
        "voice_identity_guaranteed": False}), encoding="utf-8")
    client.delete(f"http://127.0.0.1:8090/owners/{owner}", headers=headers).raise_for_status()
print("Real local inference produced a decodable audio file; inference inputs erased.")
