"""One cancellable inference process. No API/database/object storage credentials."""

import json
import os
import sys
from pathlib import Path

folder = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from app.audio_worker import resource_limits

resource_limits(49152, 1800, max_processes=8, file_mib=512)
request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
if request.get("operation") == "separate":
    from demucs.separate import main

    main(
        [
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs",
            "-d",
            "cpu",
            "--out",
            str(folder / "stems"),
            str(folder / "reference.wav"),
        ]
    )
    import shutil

    shutil.copyfile(
        folder / "stems/htdemucs/reference/vocals.wav", folder / "output.wav"
    )
else:
    import torch
    from acestep.pipeline_ace_step import ACEStepPipeline

    torch.set_num_threads(min(16, os.cpu_count() or 4))
    pipeline = ACEStepPipeline(
        checkpoint_dir=os.environ["DEEPVOCAL_SINGING_MODELS"],
        dtype="float32",
        torch_compile=False,
        cpu_offload=False,
    )
    pipeline(
        format="wav",
        audio_duration=request["duration_seconds"],
        prompt=request["style"],
        lyrics=request["lyrics"],
        infer_step=request.get("steps", 12),
        manual_seeds=str(request["seed"]),
        audio2audio_enable=True,
        ref_audio_input=str(folder / "reference.wav"),
        ref_audio_strength=0.5,
        save_path=str(folder / "output.wav"),
        batch_size=1,
    )
