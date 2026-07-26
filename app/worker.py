"""Long-running process: keeps Telethon accounts connected, watches assigned
channels for new posts, and drives the comment scheduler. Run with:

    python -m app.worker
"""
from __future__ import annotations

import asyncio
import logging

from app.services.orchestrator import handle_new_post, reconcile_orphaned_comments, scheduler
from app.services.parser import execute_parse_run, get_next_queued_run_id, reconcile_orphaned_runs
from app.services.seed import seed_builtin_personas
from app.services.sync import refresh_connections_and_watchers
from app.services.telegram_manager import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 60
PARSE_POLL_INTERVAL_SECONDS = 10


async def _refresh_loop() -> None:
    while True:
        try:
            await refresh_connections_and_watchers()
        except Exception:  # noqa: BLE001
            logger.exception("Error while refreshing connections/watchers")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def _parse_loop() -> None:
    """Picks up the oldest queued lead-gen ParseRun and executes it. The web
    process only ever creates 'queued' rows — execution happens here because
    it needs long-lived Telethon connections for many sequential calls.
    execute_parse_run's own lock keeps this to one run at a time."""
    while True:
        try:
            run_id = await get_next_queued_run_id()
            if run_id is not None:
                await execute_parse_run(run_id)
        except Exception:  # noqa: BLE001
            logger.exception("Error while running parse job")
        await asyncio.sleep(PARSE_POLL_INTERVAL_SECONDS)


async def main() -> None:
    await seed_builtin_personas()
    await reconcile_orphaned_comments()
    await reconcile_orphaned_runs()
    manager.set_new_post_handler(handle_new_post)
    scheduler.start()

    logger.info("Worker starting, initial sync...")
    await refresh_connections_and_watchers()
    logger.info("Initial sync complete.")

    try:
        await asyncio.gather(_refresh_loop(), _parse_loop())
    finally:
        scheduler.shutdown(wait=False)
        await manager.disconnect_all()


if __name__ == "__main__":
    asyncio.run(main())
