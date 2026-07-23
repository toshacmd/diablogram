"""Telethon plumbing: per-account connections, proxies, sessions, sending,
joining, and per-channel "new post" watchers.

Business logic (which accounts comment, delays, AI generation, filtering,
ban/limit bookkeeping) lives in app.services.scheduler — this module only
talks to Telegram.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import socks
from telethon import TelegramClient, events, functions
from telethon.errors import (
    AuthKeyUnregisteredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    PeerFloodError,
    UserBannedInChannelError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.utils import get_peer_id

from app.config import get_settings
from app.crypto import decrypt
from app.services.exceptions import AccountBannedError, AccountLimitedError

logger = logging.getLogger(__name__)

_PROXY_TYPES = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}

NewPostHandler = Callable[[int, Message], Awaitable[None]]


class TelegramManager:
    def __init__(self) -> None:
        settings = get_settings()
        self._api_id = settings.telegram_api_id
        self._api_hash = settings.telegram_api_hash
        self._clients: dict[int, TelegramClient] = {}
        self._watchers: dict[int, int] = {}  # channel_tg_id -> account_id currently watching it
        self._handlers: dict[int, Callable] = {}  # channel_tg_id -> bound handler (for removal)
        self._on_new_post: NewPostHandler | None = None

    def set_new_post_handler(self, handler: NewPostHandler) -> None:
        self._on_new_post = handler

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    def _build_client(self, account) -> TelegramClient:
        proxy = None
        if account.proxy_type and account.proxy_host and account.proxy_port:
            proxy = (
                _PROXY_TYPES[account.proxy_type.lower()],
                account.proxy_host,
                account.proxy_port,
                True,
                account.proxy_username or None,
                decrypt(account.proxy_password_enc) if account.proxy_password_enc else None,
            )
        session = StringSession(decrypt(account.session_string_enc))
        return TelegramClient(session, self._api_id, self._api_hash, proxy=proxy)

    async def connect_account(self, account) -> None:
        """(Re)connect a single account's client. Safe to call repeatedly."""
        existing = self._clients.get(account.id)
        if existing is not None and existing.is_connected():
            return
        if existing is not None:
            await existing.disconnect()

        client = self._build_client(account)
        await client.connect()
        if not await client.is_user_authorized():
            raise AccountBannedError(f"Account {account.id} session is not authorized")
        self._clients[account.id] = client
        logger.info("Connected account %s (%s)", account.id, account.label)

    async def disconnect_account(self, account_id: int) -> None:
        client = self._clients.pop(account_id, None)
        if client is not None:
            await client.disconnect()

    async def disconnect_all(self) -> None:
        for account_id in list(self._clients):
            await self.disconnect_account(account_id)

    def get_client(self, account_id: int) -> TelegramClient:
        client = self._clients.get(account_id)
        if client is None:
            raise AccountBannedError(f"No connected client for account {account_id}")
        return client

    def is_connected(self, account_id: int) -> bool:
        client = self._clients.get(account_id)
        return client is not None and client.is_connected()

    # ------------------------------------------------------------------ #
    # Channel watching (new-post detection)
    # ------------------------------------------------------------------ #

    async def set_watcher(self, channel_tg_id: int, account_id: int) -> None:
        """Assign `account_id` as the listener for new posts on `channel_tg_id`,
        replacing any previous watcher for that channel."""
        if self._watchers.get(channel_tg_id) == account_id:
            return

        previous_id = self._watchers.get(channel_tg_id)
        if previous_id is not None:
            self._remove_handler(channel_tg_id, previous_id)

        client = self.get_client(account_id)

        async def _handler(event: events.NewMessage.Event) -> None:
            await self._dispatch_new_post(channel_tg_id, event)

        client.add_event_handler(_handler, events.NewMessage(chats=channel_tg_id))
        self._handlers[channel_tg_id] = _handler
        self._watchers[channel_tg_id] = account_id
        logger.info("Account %s is now watching channel %s", account_id, channel_tg_id)

    def _remove_handler(self, channel_tg_id: int, account_id: int) -> None:
        client = self._clients.get(account_id)
        handler = self._handlers.pop(channel_tg_id, None)
        if client is not None and handler is not None:
            client.remove_event_handler(handler)

    def clear_watchers(self) -> None:
        for channel_tg_id, account_id in list(self._watchers.items()):
            self._remove_handler(channel_tg_id, account_id)
        self._watchers.clear()

    async def _dispatch_new_post(self, channel_tg_id: int, event: events.NewMessage.Event) -> None:
        message = event.message
        if not getattr(message, "post", False):
            return  # not a broadcast channel post
        if self._on_new_post is not None:
            await self._on_new_post(channel_tg_id, message)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    async def join_channel(self, account_id: int, username_or_id: str | int):
        client = self.get_client(account_id)
        try:
            entity = await client.get_entity(username_or_id)
            await client(functions.channels.JoinChannelRequest(entity))
            return entity
        except FloodWaitError as e:
            raise AccountLimitedError(e.seconds) from e
        except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
            raise AccountBannedError(str(e)) from e

    async def resolve_channel(self, account_id: int, username_or_link: str):
        client = self.get_client(account_id)
        return await client.get_entity(username_or_link)

    async def send_comment(self, account_id: int, channel_tg_id: int, post_message_id: int, text: str) -> int:
        """Post `text` as a comment on `post_message_id` in `channel_tg_id`'s
        linked discussion group. Returns the new message id."""
        client = self.get_client(account_id)
        try:
            sent = await client.send_message(channel_tg_id, text, comment_to=post_message_id)
            return sent.id
        except FloodWaitError as e:
            raise AccountLimitedError(e.seconds) from e
        except PeerFloodError as e:
            raise AccountLimitedError(3600) from e
        except (
            UserBannedInChannelError,
            ChatWriteForbiddenError,
            UserDeactivatedBanError,
            UserDeactivatedError,
            AuthKeyUnregisteredError,
        ) as e:
            raise AccountBannedError(str(e)) from e


async def resolve_channel_standalone(account, username_or_link: str) -> tuple[int, str, str | None]:
    """One-off channel resolution using a throwaway connection — used by the
    web process, which (unlike the worker) doesn't keep accounts connected.

    Returns (marked_tg_channel_id, title, username). The marked id (Telethon's
    get_peer_id form, e.g. -100xxxxxxxxxx) is what must be stored and reused
    for events/sending, per Telethon's own recommendation for id stability.
    """
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        entity = await temp.resolve_channel(account.id, username_or_link)
        return get_peer_id(entity), entity.title, getattr(entity, "username", None)
    finally:
        await temp.disconnect_all()


async def join_channel_standalone(account, username_or_link: str | int) -> None:
    """One-off join using a throwaway connection, mirroring resolve_channel_standalone."""
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        await temp.join_channel(account.id, username_or_link)
    finally:
        await temp.disconnect_all()


manager = TelegramManager()
