"""Offline F0 benchmark against existing vocadito annotations; no cloning or training."""
import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from app.audio import VERSION, decode_audio, track  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--output", type=Path, default=ROOT / "_qa" / "audio-benchmark.json")
    args = parser.parse_args()
    rows = [json.loads(line) for line in (ROOT / "datasets/normalized/audio_inventory.jsonl").read_text(encoding="utf-8").splitlines()]
    results = []
    for row in rows[:args.limit]:
        with zipfile.ZipFile(ROOT / "datasets" / row["archive_path"]) as archive:
            data = archive.read(row["archive_member"])
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("benchmark audio hash mismatch")
            annotation = np.loadtxt(io.BytesIO(archive.read(row["f0_annotation_member"])), delimiter=",")
        samples, rate = decode_audio(data)
        measured = track(samples, rate)
        # Nearest annotation frame: do not interpolate voiced F0 through unvoiced gaps.
        times = np.array(measured["times"])
        right = np.searchsorted(annotation[:, 0], times).clip(0, len(annotation) - 1)
        left = np.maximum(0, right - 1)
        nearest = np.where(abs(annotation[left, 0] - times) <= abs(annotation[right, 0] - times), left, right)
        expected = annotation[nearest, 1]
        observed = np.array([f or 0 for f in measured["f0"]])
        expected_voice, observed_voice = expected > 0, observed > 0
        common = expected_voice & observed_voice
        cents = 1200 * np.log2(observed[common] / expected[common])
        denominator = max(1, int(expected_voice.sum()))
        results.append({"clip": row["clip_id"], "voicing_recall": float(common.sum() / denominator),
            "voicing_false_alarm": float((observed_voice & ~expected_voice).sum() / max(1, int((~expected_voice).sum()))),
            "raw_pitch_accuracy_50_cents": float((abs(cents) <= 50).sum() / denominator),
            "median_absolute_cents_on_joint_voiced": float(np.median(abs(cents))) if cents.size else None})
    summary = {"algorithm": VERSION, "dataset": "vocadito", "scope": "offline engineering F0 test; not vocal-quality labels or Chinese song evaluation",
        "count": len(results), "macro_raw_pitch_accuracy_50_cents": float(np.mean([r["raw_pitch_accuracy_50_cents"] for r in results])), "clips": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "clips"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
