"""Lead-gen channel discovery: keyword search + recursive "similar channels"
expansion, filtered down to RU channels with open comments. Executed by the
worker process (needs long-lived Telethon connections for many sequential
calls per run) — the web process only creates/reads ParseRun rows.

Every DB touch here opens its own short-lived session rather than threading
one session through the whole (potentially many-minutes-long) run — this
project has twice been bitten by DetachedInstanceError from ORM objects
outliving their session (see telegram_manager.py / orchestrator.py history),
so accounts are tracked here as plain ids, not long-lived ORM instances.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re

from sqlalchemy import select
from telethon.utils import get_peer_id

from app.db import async_session_factory
from app.models import AccountStatus, ParsedChannel, ParseRun, ParseRunStatus, ScrapeAccount
from app.services.exceptions import AccountBannedError, AccountLimitedError
from app.services.telegram_manager import TelegramManager

logger = logging.getLogger(__name__)

# Separate instance from telegram_manager.manager (the comment pool).
# TelegramManager keys live connections by plain account.id, and
# ScrapeAccount has its own independent id sequence starting at 1 — sharing
# the comment pool's singleton would let a scrape account's connection
# collide with a same-numbered comment account's.
scrape_manager = TelegramManager()

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_LETTER_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]")

_CALL_PACING_SECONDS = 2.5
_MAX_SEEDS_PER_LEVEL = 50
_SEARCH_RESULT_LIMIT = 50

# Only one parse run active at a time — matches the "manual, modest runs"
# decision; a second queued run just waits for this to release.
_run_lock = asyncio.Lock()


def _looks_russian(*texts: str | None) -> bool:
    letters = []
    for text in texts:
        if text:
            letters.extend(_LETTER_RE.findall(text))
    if not letters:
        return False
    cyrillic = sum(1 for c in letters if _CYRILLIC_RE.match(c))
    return cyrillic / len(letters) > 0.5


class _RunContext:
    """Per-run round-robin over scrape account ids, with an in-memory
    cooldown overlay (not the ORM objects — see module docstring)."""

    def __init__(self, account_ids: list[int], min_subscribers: int, max_inactive_days: int):
        self.min_subscribers = min_subscribers
        self.max_inactive_days = max_inactive_days
        self.seen_ids: set[int] = set()
        self._account_ids = account_ids
        self._index = 0
        self._unavailable_until: dict[int, dt.datetime] = {}
        self._banned: set[int] = set()

    def next_account(self) -> int | None:
        now = dt.datetime.now(dt.timezone.utc)
        for _ in range(len(self._account_ids)):
            aid = self._account_ids[self._index % len(self._account_ids)]
            self._index += 1
            if aid in self._banned:
                continue
            until = self._unavailable_until.get(aid)
            if until is None or until <= now:
                return aid
        return None

    def mark_limited(self, account_id: int, seconds: int) -> None:
        self._unavailable_until[account_id] = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)

    def mark_banned(self, account_id: int) -> None:
        self._banned.add(account_id)


async def _persist_limited(account_id: int, seconds: int) -> None:
    async with async_session_factory() as session:
        account = await session.get(ScrapeAccount, account_id)
        if account is not None:
            account.status = AccountStatus.LIMITED
            account.limited_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
            await session.commit()


async def _persist_banned(account_id: int, note: str) -> None:
    async with async_session_factory() as session:
        account = await session.get(ScrapeAccount, account_id)
        if account is not None:
            account.status = AccountStatus.BANNED
            account.status_note = note
            await session.commit()


async def _save_channel(run_id: int, channel, subscriber_count: int, found_via: str) -> None:
    async with async_session_factory() as session:
        run = await session.get(ParseRun, run_id)
        if run is None:
            return
        session.add(
            ParsedChannel(
                run_id=run_id,
                tg_channel_id=get_peer_id(channel),
                title=channel.title,
                username=channel.username,
                subscriber_count=subscriber_count,
                found_via=found_via,
            )
        )
        run.channels_found += 1
        await session.commit()


async def _consider_candidate(run_id: int, ctx: _RunContext, account_id: int, channel, found_via: str) -> bool:
    """Cheap-to-expensive filter chain. Returns True (and persists a
    ParsedChannel row) iff the channel survives every filter — meaning it's
    also a good seed for the next expansion level."""
    marked_id = get_peer_id(channel)
    if marked_id in ctx.seen_ids:
        return False
    ctx.seen_ids.add(marked_id)

    if not channel.username:
        return False  # no public link — can't be visited manually, useless as a lead
    if (channel.participants_count or 0) < ctx.min_subscribers:
        return False
    if not _looks_russian(channel.title):
        return False

    try:
        full = await scrape_manager.get_channel_full_info(account_id, channel)
    except AccountLimitedError as e:
        ctx.mark_limited(account_id, e.retry_after_seconds)
        await _persist_limited(account_id, e.retry_after_seconds)
        return False
    except AccountBannedError as e:
        ctx.mark_banned(account_id)
        await _persist_banned(account_id, str(e))
        return False

    if not full.linked_chat_id:
        return False  # comments not open — the whole point of this tool
    if not _looks_russian(channel.title, full.about):
        return False

    try:
        last_post = await scrape_manager.get_last_post_date(account_id, channel)
    except AccountLimitedError as e:
        ctx.mark_limited(account_id, e.retry_after_seconds)
        await _persist_limited(account_id, e.retry_after_seconds)
        return False
    except AccountBannedError as e:
        ctx.mark_banned(account_id)
        await _persist_banned(account_id, str(e))
        return False

    if last_post is None:
        return False
    if (dt.datetime.now(dt.timezone.utc) - last_post) > dt.timedelta(days=ctx.max_inactive_days):
        return False

    subscriber_count = full.participants_count or channel.participants_count or 0
    await _save_channel(run_id, channel, subscriber_count, found_via)
    return True


async def _set_run_status(run_id: int, status: ParseRunStatus, note: str | None = None) -> None:
    async with async_session_factory() as session:
        run = await session.get(ParseRun, run_id)
        if run is not None:
            run.status = status
            run.status_note = note
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            await session.commit()


async def execute_parse_run(run_id: int) -> None:
    async with _run_lock:
        await _execute_parse_run(run_id)


async def _execute_parse_run(run_id: int) -> None:
    async with async_session_factory() as session:
        run = await session.get(ParseRun, run_id)
        if run is None:
            return
        run.status = ParseRunStatus.RUNNING
        run.started_at = dt.datetime.now(dt.timezone.utc)
        keywords_raw = run.keywords
        min_subscribers = run.min_subscribers
        max_inactive_days = run.max_inactive_days
        depth = run.depth
        await session.commit()

        accounts = (
            (await session.execute(select(ScrapeAccount).where(ScrapeAccount.status != AccountStatus.DISABLED)))
            .scalars()
            .all()
        )

    if not accounts:
        await _set_run_status(run_id, ParseRunStatus.FAILED, "Нет доступных scrape-аккаунтов")
        return

    for account in accounts:
        try:
            await scrape_manager.connect_account(account)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to connect scrape account %s", account.id)

    connected_ids = [a.id for a in accounts if scrape_manager.is_connected(a.id)]
    if not connected_ids:
        await _set_run_status(run_id, ParseRunStatus.FAILED, "Ни один scrape-аккаунт не смог подключиться")
        return

    # Auto-detect Premium now that we're connected — informational only,
    # affects how many similar-channel recommendations Telegram returns.
    async with async_session_factory() as session:
        for account_id in connected_ids:
            try:
                me = await scrape_manager.get_me(account_id)
            except Exception:  # noqa: BLE001
                continue
            db_account = await session.get(ScrapeAccount, account_id)
            if db_account is not None:
                db_account.is_premium = bool(getattr(me, "premium", False))
        await session.commit()

    ctx = _RunContext(connected_ids, min_subscribers, max_inactive_days)
    keywords = [kw.strip() for kw in re.split(r"[,\n]", keywords_raw) if kw.strip()]

    exhausted_early = False

    try:
        seeds: list = []
        for keyword in keywords:
            account_id = ctx.next_account()
            if account_id is None:
                exhausted_early = True
                break
            try:
                results = await scrape_manager.search_channels(account_id, keyword, limit=_SEARCH_RESULT_LIMIT)
            except AccountLimitedError as e:
                ctx.mark_limited(account_id, e.retry_after_seconds)
                await _persist_limited(account_id, e.retry_after_seconds)
                continue
            except AccountBannedError as e:
                ctx.mark_banned(account_id)
                await _persist_banned(account_id, str(e))
                continue
            for channel in results:
                if await _consider_candidate(run_id, ctx, account_id, channel, "keyword"):
                    seeds.append(channel)
            await asyncio.sleep(_CALL_PACING_SECONDS)

        for _level in range(depth):
            current_seeds, seeds = seeds[:_MAX_SEEDS_PER_LEVEL], []
            if not current_seeds:
                break
            for seed_channel in current_seeds:
                account_id = ctx.next_account()
                if account_id is None:
                    exhausted_early = True
                    break
                try:
                    results = await scrape_manager.get_similar_channels(account_id, seed_channel)
                except AccountLimitedError as e:
                    ctx.mark_limited(account_id, e.retry_after_seconds)
                    await _persist_limited(account_id, e.retry_after_seconds)
                    continue
                except AccountBannedError as e:
                    ctx.mark_banned(account_id)
                    await _persist_banned(account_id, str(e))
                    continue
                for channel in results:
                    if await _consider_candidate(run_id, ctx, account_id, channel, "similar"):
                        seeds.append(channel)
                await asyncio.sleep(_CALL_PACING_SECONDS)

        note = (
            "Завершён досрочно: все scrape-аккаунты стали недоступны (лимит/бан) — "
            "обработаны не все ключевые слова/похожие каналы"
            if exhausted_early
            else None
        )
        await _set_run_status(run_id, ParseRunStatus.COMPLETED, note)
    except Exception as e:  # noqa: BLE001
        logger.exception("Parse run %s crashed", run_id)
        await _set_run_status(run_id, ParseRunStatus.FAILED, str(e))
    finally:
        await scrape_manager.disconnect_all()


async def reconcile_orphaned_runs() -> None:
    """Mark stale `running` runs as failed on worker startup.

    A run only executes inside this worker process — if it restarts (deploy,
    crash) mid-run, the row stays `running` forever: the poll loop only picks
    up `queued` rows, so nothing would ever finish or retry it. Mirrors
    reconcile_orphaned_comments in orchestrator.py."""
    async with async_session_factory() as session:
        stale = (
            await session.execute(select(ParseRun).where(ParseRun.status == ParseRunStatus.RUNNING))
        ).scalars().all()
        for run in stale:
            run.status = ParseRunStatus.FAILED
            run.status_note = "Воркер был перезапущен во время прогона — запустите прогон заново"
            run.finished_at = dt.datetime.now(dt.timezone.utc)
        if stale:
            await session.commit()
            logger.info("Marked %d orphaned running parse run(s) as failed after restart", len(stale))


async def get_next_queued_run_id() -> int | None:
    async with async_session_factory() as session:
        run = (
            await session.execute(
                select(ParseRun).where(ParseRun.status == ParseRunStatus.QUEUED).order_by(ParseRun.created_at)
            )
        ).scalars().first()
        return run.id if run else None
