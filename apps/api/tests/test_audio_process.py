import asyncio
import os
import subprocess
import sys

import pytest
from test_audio import wav

from app.audio import AssessmentInput
from app.audio_process import assess_isolated
from app.config import Settings


def test_real_decoder_is_separate_limited_process_and_cleans_private_files(tmp_path):
    result = asyncio.run(
        assess_isolated(
            wav(),
            AssessmentInput(profile="long_tone", goal_duration_ms=4000),
            Settings(audio_runtime_root=str(tmp_path)),
        )
    )
    assert result["total"]["value"] > 95
    assert result["execution"]["pid"] != os.getpid()
    assert result["execution"]["resource_limits"] in {"windows-job-object", "linux-rlimit"}
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match="AUDIO_DECODE_FAILED"):
        asyncio.run(
            assess_isolated(
                b"not an audio", AssessmentInput(), Settings(audio_runtime_root=str(tmp_path))
            )
        )
    assert list(tmp_path.iterdir()) == []


def test_deadline_and_cancellation_kill_process_and_strip_secrets(tmp_path, monkeypatch):
    launched = []
    real_popen = subprocess.Popen
    monkeypatch.setenv("MODEL_API_KEY", "must-not-reach-decoder")

    def slow(*args, **kwargs):
        assert "MODEL_API_KEY" not in kwargs["env"] and "DATABASE_URL" not in kwargs["env"]
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr("app.audio_process.launch_decoder", slow)
    settings = Settings(audio_runtime_root=str(tmp_path), audio_process_timeout_seconds=1)
    with pytest.raises(ValueError, match="AUDIO_PROCESS_TIMEOUT"):
        asyncio.run(assess_isolated(b"test", AssessmentInput(), settings))
    assert launched[-1].poll() is not None and not list(tmp_path.iterdir())

    async def cancelled():
        task = asyncio.create_task(assess_isolated(b"test", AssessmentInput(), settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancelled())
    assert launched[-1].poll() is not None and not list(tmp_path.iterdir())
