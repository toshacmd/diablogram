"""Reconciles the running TelegramManager (connections + watchers) with the
current database state. Called on worker startup and polled periodically so
that changes made in the web panel (new account, new assignment, etc.) take
effect without restarting the worker process.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.db import async_session_factory
from app.models import Account, AccountChannelAssignment, AccountStatus, Channel, JoinStatus
from app.services import notifier
from app.services.exceptions import AccountBannedError, AccountLimitedError, JoinRequestPendingError
from app.services.telegram_manager import manager
from app.services.timeutil import ensure_aware

logger = logging.getLogger(__name__)

# Channels currently missing a watcher — tracked so the warning logs only on
# the transition into that state, not every 60s sync cycle it persists.
_channels_without_watcher: set[int] = set()

# Pause between join attempts in process_pending_joins — joining many
# channels back-to-back with no pacing (e.g. after a large "select all"
# batch) was enough to trip Telegram's flood-wait protection.
_JOIN_PACING_SECONDS = 3

# Connecting through a dead proxy can hang far longer than the sync interval;
# cap it so one bad account can't stall the whole cycle (watchers, joins).
_CONNECT_TIMEOUT_SECONDS = 30


async def refresh_connections_and_watchers() -> None:
    async with async_session_factory() as session:
        channels = (await session.execute(select(Channel).where(Channel.is_active == True))).scalars().all()  # noqa: E712
        assignments = (await session.execute(select(AccountChannelAssignment))).scalars().all()

        accounts_by_channel: dict[int, list[int]] = {}
        for a in assignments:
            accounts_by_channel.setdefault(a.channel_id, []).append(a.account_id)

        needed_account_ids: set[int] = set()
        for channel in channels:
            needed_account_ids.update(accounts_by_channel.get(channel.id, []))

        accounts_by_id: dict[int, Account] = {}
        if needed_account_ids:
            rows = (
                (await session.execute(select(Account).where(Account.id.in_(needed_account_ids))))
                .scalars()
                .all()
            )
            accounts_by_id = {a.id: a for a in rows}

        should_connect = {
            account_id: account
            for account_id, account in accounts_by_id.items()
            if account.status not in (AccountStatus.BANNED, AccountStatus.DISABLED)
        }

        # Drop connections that are no longer wanted: deleted/unassigned
        # accounts, banned/disabled ones, accounts whose channels went
        # inactive. Without this they stayed connected until a worker restart.
        for account_id in manager.known_account_ids():
            if account_id not in should_connect:
                await manager.disconnect_account(account_id)
                logger.info("Disconnected account %s (no longer needed)", account_id)

        # Connect everything needed. connect_account itself no-ops when the
        # client is already connected with unchanged session/proxy, and
        # rebuilds it when the panel changed them.
        for account_id, account in should_connect.items():
            try:
                await asyncio.wait_for(manager.connect_account(account), timeout=_CONNECT_TIMEOUT_SECONDS)
            except AccountBannedError as e:
                account.status = AccountStatus.BANNED
                account.status_note = str(e)
                await session.commit()
                await notifier.notify_account_banned(account.label, str(e))
            except asyncio.TimeoutError:
                logger.warning("Connecting account %s timed out after %ss", account_id, _CONNECT_TIMEOUT_SECONDS)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to connect account %s", account_id)

        # Assign one watcher per active channel: prefer a fully ACTIVE, connected account.
        for channel in channels:
            candidates = [
                accounts_by_id[aid]
                for aid in accounts_by_channel.get(channel.id, [])
                if aid in accounts_by_id and manager.is_connected(aid)
            ]
            active = sorted((a for a in candidates if a.status == AccountStatus.ACTIVE), key=lambda a: a.id)
            limited = sorted((a for a in candidates if a.status == AccountStatus.LIMITED), key=lambda a: a.id)
            watcher = (active or limited or [None])[0]

            if watcher is None:
                if channel.id not in _channels_without_watcher:
                    logger.warning(
                        "No connected account available to watch channel %s (%s)", channel.title, channel.id
                    )
                    _channels_without_watcher.add(channel.id)
                continue

            _channels_without_watcher.discard(channel.id)
            await manager.set_watcher(channel.tg_channel_id, watcher.id)

    await process_pending_joins()


async def process_pending_joins() -> None:
    """Joins accounts into their newly-assigned channels' discussion groups
    in the background, reusing whatever connection the loop above already
    keeps open. Replaces the old synchronous join-on-save flow in the web
    panel, which was slow enough per-channel that a large "select all
    channels" batch could trip nginx's gateway timeout."""
    async with async_session_factory() as session:
        pending = (
            (
                await session.execute(
                    select(AccountChannelAssignment)
                    .options(
                        joinedload(AccountChannelAssignment.account), joinedload(AccountChannelAssignment.channel)
                    )
                    .where(AccountChannelAssignment.join_status == JoinStatus.PENDING)
                )
            )
            .scalars()
            .all()
        )

        limited_this_cycle: set[int] = set()
        now = dt.datetime.now(dt.timezone.utc)

        for assignment in pending:
            account = assignment.account
            channel = assignment.channel
            if not manager.is_connected(account.id) or account.id in limited_this_cycle:
                continue  # not connected, or already flood-limited this cycle — retried next cycle

            # A flood-wait from a previous cycle is still ticking — retrying
            # before it expires just earns a fresh (often longer) flood-wait,
            # keeping the account unusable for commenting indefinitely.
            limited_until = ensure_aware(account.limited_until)
            if account.status == AccountStatus.LIMITED and limited_until and limited_until > now:
                continue

            # Re-check the row is still pending right before acting: the
            # panel's "cancel pending joins" button deletes these rows and
            # may do so mid-cycle, while this loop is pacing through a batch.
            still_pending = (
                await session.execute(
                    select(AccountChannelAssignment.id).where(
                        AccountChannelAssignment.id == assignment.id,
                        AccountChannelAssignment.join_status == JoinStatus.PENDING,
                    )
                )
            ).scalar_one_or_none()
            if still_pending is None:
                continue

            target = channel.username or channel.tg_channel_id
            try:
                await manager.join_channel(account.id, target, invite_link=channel.invite_link)
            except JoinRequestPendingError:
                assignment.join_status = JoinStatus.PENDING_APPROVAL
            except AccountLimitedError as e:
                account.status = AccountStatus.LIMITED
                account.limited_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=e.retry_after_seconds)
                account.status_note = f"Флуд-лимит при вступлении в «{channel.title}» ({e.retry_after_seconds}s)"
                limited_this_cycle.add(account.id)  # stop hammering it — the rest retry next cycle
                # join_status stays PENDING — retried automatically once active again
            except AccountBannedError as e:
                account.status = AccountStatus.BANNED
                account.status_note = str(e)
                assignment.join_status = JoinStatus.FAILED
                assignment.join_error = str(e)
                await notifier.notify_account_banned(account.label, str(e))
            except Exception as e:  # noqa: BLE001
                logger.exception("Failed to join account %s to channel %s", account.id, channel.id)
                assignment.join_status = JoinStatus.FAILED
                assignment.join_error = str(e)
            else:
                assignment.join_status = JoinStatus.JOINED
                assignment.join_error = None
            await session.commit()
            await asyncio.sleep(_JOIN_PACING_SECONDS)
