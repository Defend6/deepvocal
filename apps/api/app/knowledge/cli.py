import argparse
import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.knowledge.filter import filter_chinese_corpus
from app.knowledge.governance import prepare
from app.knowledge.service import KnowledgeService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["filter-zh", "prepare", "ingest"])
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="prepare an empty disabled bundle if no corpus is mounted; never ignore partial imports",
    )
    args = parser.parse_args()
    settings = get_settings()
    if args.action == "filter-zh":
        result = filter_chinese_corpus(
            Path(settings.corpus_root),
            Path(settings.corpus_root) / "working" / "zh-filtered-candidates.json",
        )
    elif args.action == "prepare":
        result = prepare(
            Path(settings.corpus_root),
            Path(settings.knowledge_bundle),
            settings.knowledge_mode,
            allow_missing=args.allow_missing,
        )
    else:
        result = asyncio.run(KnowledgeService(settings).ingest())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
