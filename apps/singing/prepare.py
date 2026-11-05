"""Explicit public model download into the project, pinned independently of source code."""

import json
import os
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HOME", str(ROOT / "runtime/model-cache/huggingface"))
MODEL_REVISION = "82cd0d7b6322bd28cd4e830fe675ddb6180ce36c"
model_path = snapshot_download(
    repo_id="ACE-Step/ACE-Step-v1-3.5B",
    revision=MODEL_REVISION,
    local_dir=ROOT / "runtime/models/ace-step",
    max_workers=4,
)
manifest = {
    "repository": "ACE-Step/ACE-Step-v1-3.5B",
    "revision": MODEL_REVISION,
    "license": "Apache-2.0",
    "path": str(model_path),
}
(ROOT / "runtime/models/ace-step-manifest.json").write_text(
    json.dumps(manifest, indent=2), encoding="utf-8"
)
print(
    "ACE-Step model files prepared locally; real inference still requires validation."
)
