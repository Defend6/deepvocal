"""Offline measurement without bypassing the server's pending-asset policy."""

import argparse
import asyncio
import json
from pathlib import Path

from app.audio import AssessmentInput
from app.audio_process import assess_isolated
from app.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.audio.stat().st_size > 104857600:
        parser.error("audio exceeds 100 MiB")
    config = AssessmentInput.model_validate_json(args.config.read_text(encoding="utf-8"))
    result = asyncio.run(assess_isolated(args.audio.read_bytes(), config, get_settings()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
    )
    print(f"Saved {result['profile']} measurement ({result['quality']}) to {args.output}")


if __name__ == "__main__":
    main()
