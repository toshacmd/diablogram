"""One-off CLI helper to authorize a Telegram account and print its
StringSession, for pasting into the web panel when adding an account.

Usage:
    python scripts/generate_session.py

Prompts for phone number / code / 2FA password interactively (standard
Telethon login flow), then prints the session string. Run this once per
account you want to add; it does not save anything to the project's database.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first (from my.telegram.org).")
        return

    async with TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash) as client:
        session_string = client.session.save()
        me = await client.get_me()
        print(f"\nAuthorized as: {me.first_name} (@{me.username})")
        print("\nSession string (paste this into the web panel when adding the account):\n")
        print(session_string)


if __name__ == "__main__":
    asyncio.run(main())
