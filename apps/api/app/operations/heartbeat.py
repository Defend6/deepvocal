import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


@dataclass
class WorkerState:
    phase: str = "idle"
    active_job_id: str | None = None
    last_error_code: str | None = None
    tick_generation: int = 0
    persisted_generation: int = 0
    reclaim_cursor: str = "0-0"


async def _write_heartbeat(sessions, consumer: str, state: WorkerState) -> bool:
    generation = state.tick_generation
    try:
        async with sessions() as session:
            await session.execute(
                text("""INSERT INTO worker_heartbeats
                (consumer,phase,active_job_id,last_error_code,last_tick_at)
                VALUES (:consumer,:phase,:job,:error,CASE WHEN :progress THEN now() END)
                ON CONFLICT(consumer) DO UPDATE SET seen_at=now(),phase=:phase,
                active_job_id=:job,last_error_code=:error,
                last_tick_at=CASE WHEN :progress THEN now() ELSE worker_heartbeats.last_tick_at END"""),
                {
                    "consumer": consumer,
                    "phase": state.phase,
                    "job": state.active_job_id,
                    "error": state.last_error_code,
                    "progress": generation > state.persisted_generation,
                },
            )
            await session.commit()
        state.persisted_generation = generation
        return True
    except SQLAlchemyError:
        logger.warning("worker_heartbeat_deferred code=DATABASE_UNAVAILABLE")
        return False


async def publish_heartbeat(sessions, consumer: str, state: WorkerState) -> bool:
    try:
        return await asyncio.wait_for(_write_heartbeat(sessions, consumer, state), timeout=4)
    except TimeoutError:
        logger.warning("worker_heartbeat_deferred code=DATABASE_UNAVAILABLE")
        return False


@asynccontextmanager
async def worker_presence(sessions, consumer: str, interval: float = 5):
    state = WorkerState()
    await publish_heartbeat(sessions, consumer, state)

    async def pulse():
        while True:
            await asyncio.sleep(interval)
            await publish_heartbeat(sessions, consumer, state)

    task = asyncio.create_task(pulse())
    try:
        yield state
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        state.phase, state.active_job_id = "stopped", None
        await publish_heartbeat(sessions, consumer, state)
