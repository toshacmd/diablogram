"""Sends account-status notifications to the owner via a plain Bot API bot
(separate from the userbot accounts that do the commenting)."""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.models import CommentStatus

logger = logging.getLogger(__name__)


async def notify_owner(text: str) -> None:
    settings = get_settings()
    if not settings.notifier_bot_token or not settings.notifier_owner_chat_id:
        logger.warning("Notifier not configured (NOTIFIER_BOT_TOKEN/NOTIFIER_OWNER_CHAT_ID missing): %s", text)
        return

    url = f"https://api.telegram.org/bot{settings.notifier_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(
                url,
                json={"chat_id": settings.notifier_owner_chat_id, "text": text},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to deliver owner notification")


async def notify_account_banned(account_label: str, reason: str) -> None:
    """Account-health alert — fired outside of any specific comment attempt
    too (e.g. from the periodic reconnect loop in sync.py), so it stays
    separate from notify_comment_result below."""
    await notify_owner(f"🚫 Аккаунт «{account_label}» заблокирован/не авторизован: {reason}")


async def notify_comment_result(account_label: str, channel_title: str, status: CommentStatus, error: str | None) -> None:
    """One notification per comment attempt, covering every terminal
    outcome — replaces the old per-failure-reason alerts (rate limited,
    banned, channel banned), which duplicated whatever this already says."""
    if status == CommentStatus.POSTED:
        await notify_owner(f"✅ «{account_label}» опубликовал комментарий в «{channel_title}»")
    elif status == CommentStatus.SKIPPED_FILTER:
        await notify_owner(f"🚫 «{account_label}»: комментарий в «{channel_title}» отфильтрован — {error}")
    else:
        await notify_owner(f"❌ «{account_label}»: ошибка комментария в «{channel_title}» — {error}")
