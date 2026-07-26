"""Executes bulk profile tasks (avatar / story / bio for many accounts) in
the worker. The web panel only creates ProfileTask + ProfileTaskItem rows and
drops the media file under data/profile_tasks/ (the shared ./data volume) —
running the Telegram round trips per account inside an HTTP request is the
same 504/flood-wait trap the synchronous channel joins fell into.

Items stay `pending` through flood-waits and retry once the limit expires;
any other failure is terminal (`failed`) — no automatic reanimation, matching
the project's account-health philosophy.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.db import async_session_factory, utc_now
from app.models import (
    AccountStatus,
    ProfileTask,
    ProfileTaskItem,
    ProfileTaskItemStatus,
    ProfileTaskKind,
)
from app.services import notifier
from app.services.exceptions import AccountBannedError, AccountLimitedError
from app.services.telegram_manager import TelegramManager, manager
from app.services.timeutil import ensure_aware

logger = logging.getLogger(__name__)

# Media payloads for pending tasks — web writes, worker reads and deletes.
MEDIA_DIR = Path("data/profile_tasks")

# Pause between accounts — same reasoning as the join pacing: hammering the
# same action across many accounts back-to-back invites flood-waits.
_PACING_SECONDS = 5

_KIND_LABELS = {
    ProfileTaskKind.AVATAR: "аватар",
    ProfileTaskKind.STORY: "сторис",
    ProfileTaskKind.BIO: "био",
}


async def _execute_item(task: ProfileTask, account, media_bytes: bytes | None) -> None:
    """One account's action. Reuses the worker's live connection when the
    account has one (it does whenever it's assigned to channels); otherwise
    opens a one-off connection just for this call."""
    temp: TelegramManager | None = None
    if manager.is_connected(account.id):
        mgr = manager
    else:
        temp = TelegramManager()
        await temp.connect_account(account)
        mgr = temp
    try:
        if task.kind == ProfileTaskKind.AVATAR:
            await mgr.update_avatar(account.id, media_bytes)
        elif task.kind == ProfileTaskKind.BIO:
            await mgr.update_profile(account.id, about=task.text or "")
        else:
            await mgr.post_story(account.id, media_bytes, task.media_filename or "story.jpg", task.text or None)
    finally:
        if temp is not None:
            await temp.disconnect_all()


async def _maybe_finish_task(session, task: ProfileTask) -> None:
    """When the last pending item of a task is done: clean up its media file
    and send the owner one summary notification (not one per account)."""
    pending = await session.scalar(
        select(func.count(ProfileTaskItem.id)).where(
            ProfileTaskItem.task_id == task.id,
            ProfileTaskItem.status == ProfileTaskItemStatus.PENDING,
        )
    )
    if pending:
        return
    if task.media_path:
        Path(task.media_path).unlink(missing_ok=True)
    done = await session.scalar(
        select(func.count(ProfileTaskItem.id)).where(
            ProfileTaskItem.task_id == task.id, ProfileTaskItem.status == ProfileTaskItemStatus.DONE
        )
    )
    failed = await session.scalar(
        select(func.count(ProfileTaskItem.id)).where(
            ProfileTaskItem.task_id == task.id, ProfileTaskItem.status == ProfileTaskItemStatus.FAILED
        )
    )
    await notifier.notify_owner(
        f"📦 Массовая задача «{_KIND_LABELS[task.kind]}» завершена: ✅ {done} / ❌ {failed}"
    )


async def process_profile_tasks() -> None:
    async with async_session_factory() as session:
        items = (
            (
                await session.execute(
                    select(ProfileTaskItem)
                    .options(joinedload(ProfileTaskItem.task), joinedload(ProfileTaskItem.account))
                    .where(ProfileTaskItem.status == ProfileTaskItemStatus.PENDING)
                    .order_by(ProfileTaskItem.id)
                )
            )
            .scalars()
            .all()
        )
        if not items:
            return

        media_cache: dict[int, bytes] = {}
        limited_this_cycle: set[int] = set()

        for item in items:
            task = item.task
            account = item.account
            if account is None or task is None:
                continue

            if account.id in limited_this_cycle:
                continue
            limited_until = ensure_aware(account.limited_until)
            if (
                account.status == AccountStatus.LIMITED
                and limited_until
                and limited_until > dt.datetime.now(dt.timezone.utc)
            ):
                continue  # flood-wait still ticking — stays pending, retried next cycle
            if account.status == AccountStatus.BANNED:
                item.status = ProfileTaskItemStatus.FAILED
                item.error = "Аккаунт в статусе «бан» — действие не выполнялось"
                item.finished_at = utc_now()
                await session.commit()
                await _maybe_finish_task(session, task)
                continue

            # Re-check right before acting — the panel's cancel button deletes
            # pending items and may do so mid-cycle.
            still_pending = (
                await session.execute(
                    select(ProfileTaskItem.id).where(
                        ProfileTaskItem.id == item.id,
                        ProfileTaskItem.status == ProfileTaskItemStatus.PENDING,
                    )
                )
            ).scalar_one_or_none()
            if still_pending is None:
                continue

            media_bytes: bytes | None = None
            if task.media_path:
                if task.id not in media_cache:
                    try:
                        media_cache[task.id] = Path(task.media_path).read_bytes()
                    except OSError:
                        item.status = ProfileTaskItemStatus.FAILED
                        item.error = "Файл задачи не найден на диске"
                        item.finished_at = utc_now()
                        await session.commit()
                        await _maybe_finish_task(session, task)
                        continue
                media_bytes = media_cache[task.id]

            try:
                await _execute_item(task, account, media_bytes)
            except AccountLimitedError as e:
                account.status = AccountStatus.LIMITED
                account.limited_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                    seconds=e.retry_after_seconds
                )
                account.status_note = (
                    f"Флуд-лимит при массовом изменении профиля ({e.retry_after_seconds}s)"
                )
                limited_this_cycle.add(account.id)
                # item stays PENDING — retried automatically after the wait
            except AccountBannedError as e:
                account.status = AccountStatus.BANNED
                account.status_note = str(e)
                item.status = ProfileTaskItemStatus.FAILED
                item.error = str(e)
                item.finished_at = utc_now()
                await notifier.notify_account_banned(account.label, str(e))
            except Exception as e:  # noqa: BLE001
                logger.exception("Profile task item %s failed (account %s)", item.id, account.id)
                item.status = ProfileTaskItemStatus.FAILED
                item.error = str(e)
                item.finished_at = utc_now()
            else:
                item.status = ProfileTaskItemStatus.DONE
                item.finished_at = utc_now()
                # Keep the panel's cached profile in sync with what was applied.
                if task.kind == ProfileTaskKind.BIO:
                    account.tg_bio = task.text
                elif task.kind == ProfileTaskKind.AVATAR and media_bytes:
                    avatar_dir = Path("data/avatars")
                    avatar_dir.mkdir(parents=True, exist_ok=True)
                    (avatar_dir / f"{account.id}.jpg").write_bytes(media_bytes)
                account.tg_synced_at = utc_now()

            await session.commit()
            await _maybe_finish_task(session, task)
            await asyncio.sleep(_PACING_SECONDS)
