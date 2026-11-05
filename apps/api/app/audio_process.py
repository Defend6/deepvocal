"""Parent-side wall deadline and cancellation; no decoder runs inside the worker process."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.process_control import terminate_tree

launch_decoder = subprocess.Popen


async def assess_isolated(data: bytes, config, settings) -> dict:
    if not data or len(data) > settings.max_audio_size_bytes:
        raise ValueError("AUDIO_SIZE_INVALID")
    root = Path(settings.audio_runtime_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="decode-", dir=root) as directory:
        folder = Path(directory)
        folder.chmod(0o700)
        (folder / "input.audio").write_bytes(data)
        (folder / "request.json").write_text(
            json.dumps(
                {
                    "assessment": config.model_dump(mode="json"),
                    "memory_mib": settings.audio_process_memory_mib,
                    "cpu_seconds": settings.audio_process_timeout_seconds,
                }
            ),
            encoding="utf-8",
        )
        environment = {
            key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ
        }
        environment.update(
            {
                "PATH": os.pathsep.join(
                    [
                        str(Path(sys.executable).parent),
                        sys.base_prefix,
                        str(Path(os.environ.get("SystemRoot", "/usr")) / "System32"),
                    ]
                ),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "TEMP": directory,
                "TMP": directory,
            }
        )
        # Popen works with Windows Selector loops; asyncio subprocess APIs do not.
        process = launch_decoder(
            [sys.executable, "-m", "app.audio_worker", directory],
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            close_fds=True,
        )
        deadline = time.monotonic() + settings.audio_process_timeout_seconds
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise ValueError("AUDIO_PROCESS_TIMEOUT")
                await asyncio.sleep(0.05)
            response = folder / "response.json"
            if (
                process.returncode != 0
                or not response.is_file()
                or response.stat().st_size > 8388608
            ):
                raise ValueError("AUDIO_PROCESS_FAILED")
            payload = json.loads(response.read_text(encoding="utf-8"))
            if not payload.get("ok"):
                raise ValueError(payload.get("error", "AUDIO_PROCESS_FAILED"))
            return payload["result"]
        finally:
            # Also runs on CancelledError from worker timeout; no orphan decoder threads.
            await asyncio.to_thread(terminate_tree, process)
