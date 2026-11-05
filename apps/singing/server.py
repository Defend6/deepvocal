"""Private CPU inference bridge for pinned ACE-Step and optional Demucs."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.process_control import terminate_tree

config = {**dotenv_values(ROOT / ".env"), **os.environ}
TOKEN = config.get("SINGING_SERVICE_TOKEN", "")
if len(TOKEN) < 32:
    raise RuntimeError(
        "configure a private SINGING_SERVICE_TOKEN of at least 32 characters"
    )
app = FastAPI(docs_url=None, redoc_url=None)
gate = asyncio.Lock()
active = {}
RUN_ROOT = ROOT / "runtime/singing-jobs"
RUN_ROOT.mkdir(parents=True, exist_ok=True)


class Generation(BaseModel):
    job_id: UUID
    owner_id: UUID
    reference_audio_base64: str = Field(max_length=140000000)
    lyrics: str = Field(min_length=1, max_length=2000)
    style: str = Field(min_length=1, max_length=500)
    duration_seconds: int = Field(ge=5, le=60)
    seed: int = Field(ge=0, le=2147483647)
    operation: str = "generate"


def authorize(request):
    if not hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {TOKEN}"
    ):
        raise HTTPException(401, "unauthorized")


@app.get("/health")
async def health(request: Request):
    authorize(request)
    manifest = ROOT / "runtime/models/ace-step-manifest.json"
    return {
        "status": "ready" if manifest.exists() else "unavailable",
        "engine": "ace-step-audio2audio",
        "voice_identity_guaranteed": False,
    }


@app.post("/generate")
async def generate(body: Generation, request: Request):
    authorize(request)
    if body.operation not in {"generate", "separate"}:
        raise HTTPException(422, "unsupported operation")
    if gate.locked():
        raise HTTPException(429, "inference busy")
    encoded = body.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(encoded, sort_keys=True).encode()).hexdigest()
    folder = RUN_ROOT / str(body.job_id)
    folder.mkdir(exist_ok=True)
    fingerprint = folder / "fingerprint.txt"
    if fingerprint.exists() and fingerprint.read_text() != digest:
        raise HTTPException(409, "job content changed")
    fingerprint.write_text(digest)
    output = folder / "output.wav"
    completed = folder / "completed"
    async with gate:
        if not completed.exists() or not output.is_file():
            output.unlink(missing_ok=True)
            completed.unlink(missing_ok=True)
            try:
                data = base64.b64decode(body.reference_audio_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(422, "invalid reference") from exc
            if not data or len(data) > 104857600:
                raise HTTPException(413, "reference too large")
            (folder / "reference.wav").write_bytes(data)
            encoded.pop("reference_audio_base64")
            (folder / "request.json").write_text(
                json.dumps(encoded, ensure_ascii=False), encoding="utf-8"
            )
            environment = {
                key: os.environ[key]
                for key in ("SystemRoot", "WINDIR", "PATH")
                if key in os.environ
            }
            environment.update(
                {
                    "HF_HOME": str(ROOT / "runtime/model-cache/huggingface"),
                    "TORCH_HOME": str(ROOT / "runtime/model-cache/torch"),
                    "DEEPVOCAL_SINGING_MODELS": str(ROOT / "runtime/models/ace-step"),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                    "TEMP": str(folder),
                    "TMP": str(folder),
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            with (folder / "inference.log").open("wb") as log:
                process = await asyncio.to_thread(
                    subprocess.Popen,
                    [
                        sys.executable,
                        str(Path(__file__).with_name("runner.py")),
                        str(folder),
                    ],
                    env=environment,
                    cwd=folder,
                    stdout=log,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32"
                    else 0,
                )
                active[str(body.job_id)] = process
                deadline = time.monotonic() + 1800
                try:
                    while process.poll() is None:
                        if (
                            time.monotonic() > deadline
                            or await request.is_disconnected()
                        ):
                            raise HTTPException(504, "inference stopped")
                        await asyncio.sleep(0.5)
                    if process.returncode or not output.is_file():
                        raise HTTPException(503, "inference unavailable")
                    completed.write_text(digest)
                finally:
                    await asyncio.to_thread(terminate_tree, process)
                    active.pop(str(body.job_id), None)
                    (folder / "reference.wav").unlink(missing_ok=True)
                    if not completed.exists():
                        output.unlink(missing_ok=True)
        if output.stat().st_size > 104857600:
            raise HTTPException(413, "output too large")
        return {
            "audio_base64": base64.b64encode(output.read_bytes()).decode(),
            "engine": "demucs-htdemucs"
            if body.operation == "separate"
            else "ace-step-audio2audio",
            "voice_identity_guaranteed": False,
        }


@app.delete("/jobs/{job_id}")
async def cancel(job_id: UUID, request: Request):
    authorize(request)
    process = active.get(str(job_id))
    if process and process.poll() is None:
        await asyncio.to_thread(terminate_tree, process)
    return {"status": "cancelled"}


@app.delete("/owners/{owner_id}")
async def erase_owner(owner_id: UUID, request: Request):
    """Erase private inference inputs, lyrics, outputs and logs after cancellation."""
    authorize(request)
    import shutil

    for folder in RUN_ROOT.iterdir():
        record = folder / "request.json"
        if not folder.is_dir() or not record.is_file():
            continue
        try:
            owner = json.loads(record.read_text(encoding="utf-8")).get("owner_id")
        except (ValueError, OSError):
            continue
        if owner != str(owner_id):
            continue
        process = active.get(folder.name)
        if process and process.poll() is None:
            raise HTTPException(409, "owner inference still active")
        shutil.rmtree(folder)
    return {"status": "erased"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090, access_log=False)
