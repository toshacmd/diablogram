"""Reconciles the running TelegramManager (connections + watchers) with the
current database state. Called on worker startup and polled periodically so
that changes made in the web panel (new account, new assignment, etc.) take
effect without restarting the worker process.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db import async_session_factory
from app.models import Account, AccountChannelAssignment, AccountStatus, Channel
from app.services import notifier
from app.services.exceptions import AccountBannedError
from app.services.telegram_manager import manager

logger = logging.getLogger(__name__)


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

        # Connect everything needed that isn't banned/disabled.
        for account_id, account in accounts_by_id.items():
            if account.status in (AccountStatus.BANNED, AccountStatus.DISABLED):
                continue
            if manager.is_connected(account_id):
                continue
            try:
                await manager.connect_account(account)
            except AccountBannedError as e:
                account.status = AccountStatus.BANNED
                account.status_note = str(e)
                await session.commit()
                await notifier.notify_account_banned(account.label, str(e))
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
                logger.warning("No connected account available to watch channel %s (%s)", channel.title, channel.id)
                continue

            await manager.set_watcher(channel.tg_channel_id, watcher.id)
