"""Long-running process: keeps Telethon accounts connected, watches assigned
channels for new posts, and drives the comment scheduler. Run with:

    python -m app.worker
"""
from __future__ import annotations

import asyncio
import logging

from app.services.orchestrator import handle_new_post, reconcile_orphaned_comments, scheduler
from app.services.seed import seed_builtin_personas
from app.services.sync import refresh_connections_and_watchers
from app.services.telegram_manager import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 60


async def _refresh_loop() -> None:
    while True:
        try:
            await refresh_connections_and_watchers()
        except Exception:  # noqa: BLE001
            logger.exception("Error while refreshing connections/watchers")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def main() -> None:
    await seed_builtin_personas()
    await reconcile_orphaned_comments()
    manager.set_new_post_handler(handle_new_post)
    scheduler.start()

    logger.info("Worker starting, initial sync...")
    await refresh_connections_and_watchers()
    logger.info("Initial sync complete.")

    try:
        await _refresh_loop()
    finally:
        scheduler.shutdown(wait=False)
        await manager.disconnect_all()


if __name__ == "__main__":
    asyncio.run(main())
