"""Sends account-status notifications to the owner via a plain Bot API bot
(separate from the userbot accounts that do the commenting)."""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

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


async def notify_account_limited(account_label: str, retry_after_seconds: int) -> None:
    minutes = max(retry_after_seconds // 60, 1)
    await notify_owner(f"⏳ Аккаунт «{account_label}» временно ограничен Telegram (~{minutes} мин).")


async def notify_account_banned(account_label: str, reason: str) -> None:
    await notify_owner(f"🚫 Аккаунт «{account_label}» заблокирован/не авторизован: {reason}")
