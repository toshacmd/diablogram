"""Business logic tying Telegram events to AI generation and publishing.

Flow: new channel post -> pick eligible accounts -> schedule delayed,
per-account comment jobs -> generate -> filter -> publish -> log.
"""
from __future__ import annotations

import datetime as dt
import logging
import random

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload
from telethon.tl.custom.message import Message

from app.db import async_session_factory
from app.models import (
    Account,
    AccountChannelAssignment,
    AccountStatus,
    Channel,
    CommentLog,
    CommentStatus,
    GlobalSettings,
)
from app.services import notifier
from app.services.ai_generator import get_comment_generator
from app.services.content_filter import check_text
from app.services.exceptions import AccountBannedError, AccountLimitedError
from app.services.telegram_manager import manager

logger = logging.getLogger(__name__)

_DEFAULT_PERSONA_PROMPT = "нейтральный, дружелюбный подписчик без выраженных особенностей речи"

scheduler = AsyncIOScheduler()


async def _get_settings_row(session) -> GlobalSettings:
    row = (await session.execute(select(GlobalSettings).where(GlobalSettings.id == 1))).scalar_one_or_none()
    if row is None:
        row = GlobalSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def _today_start_utc() -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _is_eligible(session, account: Account) -> bool:
    if account.status == AccountStatus.BANNED or account.status == AccountStatus.DISABLED:
        return False

    if account.status == AccountStatus.LIMITED:
        if account.limited_until and account.limited_until <= dt.datetime.now(dt.timezone.utc):
            account.status = AccountStatus.ACTIVE
            account.limited_until = None
            await session.commit()
        else:
            return False

    posted_today = await session.scalar(
        select(func.count(CommentLog.id)).where(
            CommentLog.account_id == account.id,
            CommentLog.status == CommentStatus.POSTED,
            CommentLog.posted_at >= _today_start_utc(),
        )
    )
    return posted_today < account.daily_comment_cap


async def handle_new_post(channel_tg_id: int, message: Message) -> None:
    post_text = (message.message or "").strip()
    if not post_text:
        logger.info("Skipping media-only post on channel %s", channel_tg_id)
        return

    async with async_session_factory() as session:
        channel = (
            await session.execute(select(Channel).where(Channel.tg_channel_id == channel_tg_id))
        ).scalar_one_or_none()
        if channel is None or not channel.is_active:
            return

        settings_row = await _get_settings_row(session)

        assigned_accounts = (
            (
                await session.execute(
                    select(Account)
                    .options(joinedload(Account.persona))
                    .join(AccountChannelAssignment, AccountChannelAssignment.account_id == Account.id)
                    .where(AccountChannelAssignment.channel_id == channel.id)
                )
            )
            .scalars()
            .all()
        )

        eligible = [a for a in assigned_accounts if await _is_eligible(session, a)]
        if not eligible:
            logger.warning("No eligible accounts to comment on channel %s (%s)", channel.title, channel_tg_id)
            await notifier.notify_owner(
                f"⚠️ Новый пост на «{channel.title}», но нет доступных аккаунтов для комментария."
            )
            return

        count = random.randint(settings_row.commenters_min, settings_row.commenters_max)
        count = max(1, min(count, len(eligible)))  # at least 1 comment is always guaranteed
        chosen = random.sample(eligible, count)

        for account in chosen:
            delay = random.randint(settings_row.delay_min_seconds, settings_row.delay_max_seconds)
            run_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)

            log_entry = CommentLog(
                account_id=account.id,
                channel_id=channel.id,
                source_post_id=message.id,
                persona_name=account.persona.name if account.persona else None,
                generated_text="",
                signature_text=account.signature,
                full_text="",
                status=CommentStatus.SCHEDULED,
                scheduled_for=run_at,
            )
            session.add(log_entry)
            await session.commit()
            await session.refresh(log_entry)

            scheduler.add_job(
                _post_comment,
                trigger="date",
                run_date=run_at,
                args=[log_entry.id, account.id, channel.id, channel_tg_id, message.id, post_text],
                id=f"comment-{log_entry.id}",
                misfire_grace_time=600,
            )


async def _post_comment(
    log_id: int,
    account_id: int,
    channel_id: int,
    channel_tg_id: int,
    post_message_id: int,
    post_text: str,
) -> None:
    async with async_session_factory() as session:
        log_entry = await session.get(CommentLog, log_id)
        account = await session.get(Account, account_id, options=[joinedload(Account.persona)])
        if log_entry is None or account is None:
            return

        if not await _is_eligible(session, account):
            log_entry.status = CommentStatus.FAILED
            log_entry.error = "Account no longer eligible at post time"
            await session.commit()
            return

        persona_prompt = account.persona.prompt_text if account.persona else _DEFAULT_PERSONA_PROMPT

        try:
            generator = get_comment_generator()
            generated_text = await generator.generate(post_text, persona_prompt)
        except Exception as e:  # noqa: BLE001
            logger.exception("AI generation failed for log %s", log_id)
            log_entry.status = CommentStatus.FAILED
            log_entry.error = f"AI generation error: {e}"
            await session.commit()
            return

        signature = (account.signature or "").strip()
        full_text = f"{generated_text.strip()} {signature}".strip() if signature else generated_text.strip()

        settings_row = await _get_settings_row(session)
        if settings_row.content_filter_enabled:
            matched = check_text(full_text, settings_row.stop_terms)
            if matched:
                log_entry.generated_text = generated_text
                log_entry.full_text = full_text
                log_entry.status = CommentStatus.SKIPPED_FILTER
                log_entry.error = f"Blocked by content filter: matched term {matched!r}"
                await session.commit()
                return

        log_entry.generated_text = generated_text
        log_entry.full_text = full_text

        try:
            message_id = await manager.send_comment(account_id, channel_tg_id, post_message_id, full_text)
        except AccountLimitedError as e:
            account.status = AccountStatus.LIMITED
            account.limited_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=e.retry_after_seconds)
            log_entry.status = CommentStatus.FAILED
            log_entry.error = f"Rate limited for {e.retry_after_seconds}s"
            await session.commit()
            await notifier.notify_account_limited(account.label, e.retry_after_seconds)
            return
        except AccountBannedError as e:
            account.status = AccountStatus.BANNED
            account.status_note = str(e)
            log_entry.status = CommentStatus.FAILED
            log_entry.error = str(e)
            await session.commit()
            await notifier.notify_account_banned(account.label, str(e))
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("Failed to send comment for log %s", log_id)
            log_entry.status = CommentStatus.FAILED
            log_entry.error = str(e)
            await session.commit()
            return

        log_entry.status = CommentStatus.POSTED
        log_entry.comment_message_id = message_id
        log_entry.posted_at = dt.datetime.now(dt.timezone.utc)
        await session.commit()
