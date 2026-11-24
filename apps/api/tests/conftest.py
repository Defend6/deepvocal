import asyncio
import os
import sys

# Psycopg's Windows async driver requires the selector event loop, like the worker.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_configure():
    # pymilvus imports python-dotenv, which searches parent directories. Keep
    # private workstation .env values out of tests, while preserving explicitly
    # supplied integration endpoints. Run before test-module collection.
    from app.config import Settings

    for name, value in Settings(_env_file=None).model_dump().items():
        os.environ.setdefault(name.upper(), str(value))
