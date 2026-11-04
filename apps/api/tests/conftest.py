import asyncio
import sys

# Psycopg's Windows async driver requires the selector event loop, like the worker.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
