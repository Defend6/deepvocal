import argparse
import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.knowledge.governance import prepare
from app.knowledge.service import KnowledgeService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "ingest"])
    args = parser.parse_args()
    settings = get_settings()
    if args.action == "prepare":
        result = prepare(
            Path(settings.corpus_root), Path(settings.knowledge_bundle), settings.knowledge_mode
        )
    else:
        result = asyncio.run(KnowledgeService(settings).ingest())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
