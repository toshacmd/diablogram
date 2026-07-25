"""Telethon plumbing: per-account connections, proxies, sessions, sending,
joining, and per-channel "new post" watchers.

Business logic (which accounts comment, delays, AI generation, filtering,
ban/limit bookkeeping) lives in app.services.scheduler — this module only
talks to Telegram.
"""
from __future__ import annotations

import io
import logging
import re
from collections.abc import Awaitable, Callable

from telethon import TelegramClient, events, functions, types
from telethon.errors import (
    AuthKeyUnregisteredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    InviteRequestSentError,
    PeerFloodError,
    UserAlreadyParticipantError,
    UserBannedInChannelError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.types import User
from telethon.utils import get_peer_id

from app.config import get_settings
from app.crypto import decrypt
from app.services.exceptions import (
    AccountBannedError,
    AccountLimitedError,
    ChannelBannedError,
    JoinRequestPendingError,
)

logger = logging.getLogger(__name__)

# python_socks (and Telethon's PySocks-compatible fallback) both accept these
# as plain strings directly — no need for the socks module's numeric constants.
_VALID_PROXY_TYPES = {"socks5", "socks4", "http"}

# Matches t.me/joinchat/<hash> and t.me/+<hash> (and telegram.me/... variants) —
# private-channel invite links, as opposed to public @usernames.
_INVITE_HASH_RE = re.compile(r"t(?:elegram)?\.me/(?:joinchat/|\+)([\w-]+)")

NewPostHandler = Callable[[int, Message], Awaitable[None]]


def extract_invite_hash(text: str | int | None) -> str | None:
    if not text:
        return None
    match = _INVITE_HASH_RE.search(str(text))
    return match.group(1) if match else None


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
            proxy_type = account.proxy_type.lower()
            if proxy_type not in _VALID_PROXY_TYPES:
                raise ValueError(f"Unknown proxy type: {account.proxy_type!r}")
            proxy = (
                proxy_type,
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

    async def join_by_invite(self, account_id: int, invite_hash: str):
        """Join (or, if already a member, just resolve) a private channel via
        its invite link hash — the only way to reach a channel that has no
        public @username and that this account has never seen before."""
        client = self.get_client(account_id)
        try:
            result = await client(functions.messages.ImportChatInviteRequest(invite_hash))
            # Depending on the invite, Telegram either returns the chat info
            # directly (an Updates-like object with `.chats`) or wraps it one
            # level deeper in `ChatInviteJoinResultOk.updates.chats`.
            if hasattr(result, "chats"):
                chats = result.chats
            elif hasattr(result, "updates") and hasattr(result.updates, "chats"):
                chats = result.updates.chats
            else:
                raise RuntimeError(
                    f"Unsupported invite join result type: {type(result).__name__} "
                    "(likely requires interactive web verification)"
                )
            return chats[0]
        except UserAlreadyParticipantError:
            info = await client(functions.messages.CheckChatInviteRequest(invite_hash))
            return info.chat
        except InviteRequestSentError as e:
            # Not a failure — this chat has "approve new members" on, so the
            # account is now pending manual approval by an admin.
            raise JoinRequestPendingError(str(e)) from e
        except FloodWaitError as e:
            raise AccountLimitedError(e.seconds) from e
        except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
            raise AccountBannedError(str(e)) from e

    async def join_channel(self, account_id: int, username_or_id: str | int, invite_link: str | None = None):
        invite_hash = extract_invite_hash(invite_link) or extract_invite_hash(username_or_id)
        client = self.get_client(account_id)
        try:
            if invite_hash:
                entity = await self.join_by_invite(account_id, invite_hash)
            else:
                entity = await client.get_entity(username_or_id)
                await client(functions.channels.JoinChannelRequest(entity))

            # Comments live in the channel's linked discussion group, not the
            # channel itself — being a channel member alone isn't enough to
            # post there (Telegram rejects with "join the discussion group
            # before commenting"). Join it too, if there is one.
            await self._join_discussion_group(client, entity)
            return entity
        except InviteRequestSentError as e:
            # Not a failure — the chat/channel has "approve new members" on,
            # so the account is now pending manual approval by an admin.
            raise JoinRequestPendingError(str(e)) from e
        except FloodWaitError as e:
            raise AccountLimitedError(e.seconds) from e
        except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
            raise AccountBannedError(str(e)) from e

    async def _join_discussion_group(self, client: TelegramClient, channel_entity) -> None:
        full = await client(functions.channels.GetFullChannelRequest(channel_entity))
        linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
        if not linked_chat_id:
            return  # comments disabled, or this entity has no separate discussion group

        discussion_chat = next((c for c in full.chats if c.id == linked_chat_id), None)
        if discussion_chat is None:
            logger.warning("Linked discussion group %s not found in GetFullChannel response", linked_chat_id)
            return

        try:
            await client(functions.channels.JoinChannelRequest(discussion_chat))
        except UserAlreadyParticipantError:
            pass

    async def resolve_channel(self, account_id: int, username_or_link: str):
        invite_hash = extract_invite_hash(username_or_link)
        if invite_hash:
            # Resolving a private channel requires joining it — there's no
            # way to fetch chat info from a bare invite hash otherwise.
            return await self.join_by_invite(account_id, invite_hash)
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
        except (UserBannedInChannelError, ChatWriteForbiddenError) as e:
            # Restricted in *this* channel/chat only (e.g. a moderator banned
            # or kicked the account) — the account itself is otherwise fine.
            raise ChannelBannedError(str(e)) from e
        except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
            # The account itself is dead — banned/deactivated by Telegram globally.
            raise AccountBannedError(str(e)) from e

    # ------------------------------------------------------------------ #
    # Telegram profile (nickname, name, bio, avatar, stories)
    # ------------------------------------------------------------------ #

    async def get_me(self, account_id: int) -> User:
        client = self.get_client(account_id)
        return await client.get_me()

    async def download_avatar(self, account_id: int) -> bytes | None:
        """Returns the account's current profile photo as JPEG bytes, or
        None if it has no avatar set."""
        client = self.get_client(account_id)
        me = await client.get_me()
        if me.photo is None:
            return None
        buf = io.BytesIO()
        result = await client.download_profile_photo(me, file=buf)
        return buf.getvalue() if result else None

    async def update_profile(
        self,
        account_id: int,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        about: str | None = None,
        username: str | None = None,
    ) -> None:
        """Updates name/bio and/or the @username. Any of these left as None
        is left untouched. Username-specific errors (taken, invalid,
        unchanged) are intentionally not caught here — they're a UI concern
        for the caller to turn into a message, not an account-health signal."""
        client = self.get_client(account_id)
        try:
            if first_name is not None or last_name is not None or about is not None:
                await client(
                    functions.account.UpdateProfileRequest(
                        first_name=first_name, last_name=last_name, about=about
                    )
                )
            if username is not None:
                await client(functions.account.UpdateUsernameRequest(username))
        except FloodWaitError as e:
            raise AccountLimitedError(e.seconds) from e
        except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
            raise AccountBannedError(str(e)) from e

    async def update_avatar(self, account_id: int, photo_bytes: bytes) -> None:
        client = self.get_client(account_id)
        try:
            uploaded = await client.upload_file(photo_bytes, file_name="avatar.jpg")
            await client(functions.photos.UploadProfilePhotoRequest(file=uploaded))
        except FloodWaitError as e:
            raise AccountLimitedError(e.seconds) from e
        except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
            raise AccountBannedError(str(e)) from e

    async def post_story(
        self, account_id: int, media_bytes: bytes, filename: str, caption: str | None = None
    ) -> None:
        client = self.get_client(account_id)
        buf = io.BytesIO(media_bytes)
        buf.name = filename  # _file_to_media reads this to tell photo from video
        try:
            _, media, _ = await client._file_to_media(buf, file_size=len(media_bytes))
            await client(
                functions.stories.SendStoryRequest(
                    peer=types.InputPeerSelf(),
                    media=media,
                    privacy_rules=[types.InputPrivacyValueAllowAll()],
                    caption=caption or None,
                )
            )
        except FloodWaitError as e:
            raise AccountLimitedError(e.seconds) from e
        except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
            raise AccountBannedError(str(e)) from e


async def resolve_channel_standalone(account, username_or_link: str) -> tuple[int, str, str | None, str | None]:
    """One-off channel resolution using a throwaway connection — used by the
    web process, which (unlike the worker) doesn't keep accounts connected.

    Returns (marked_tg_channel_id, title, username, invite_link). The marked id
    (Telethon's get_peer_id form, e.g. -100xxxxxxxxxx) is what must be stored
    and reused for events/sending, per Telethon's own recommendation for id
    stability. invite_link is only set when `username_or_link` was itself an
    invite link — save it on the Channel so future accounts can join too
    (a bare numeric id can't be resolved by an account that's never seen it).
    """
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        entity = await temp.resolve_channel(account.id, username_or_link)
        invite_link = username_or_link if extract_invite_hash(username_or_link) else None
        return get_peer_id(entity), entity.title, getattr(entity, "username", None), invite_link
    finally:
        await temp.disconnect_all()


async def join_channel_standalone(account, username_or_id: str | int, invite_link: str | None = None) -> None:
    """One-off join using a throwaway connection, mirroring resolve_channel_standalone."""
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        await temp.join_channel(account.id, username_or_id, invite_link=invite_link)
    finally:
        await temp.disconnect_all()


async def sync_profile_standalone(account) -> tuple[User, bytes | None]:
    """One-off fetch of the account's live Telegram profile + avatar, mirroring
    resolve_channel_standalone."""
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        me = await temp.get_me(account.id)
        avatar = await temp.download_avatar(account.id)
        return me, avatar
    finally:
        await temp.disconnect_all()


async def update_profile_standalone(
    account,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    about: str | None = None,
    username: str | None = None,
) -> User:
    """One-off profile/username update, then re-fetches the resulting profile
    so the caller can refresh its cache with what Telegram actually stored."""
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        await temp.update_profile(
            account.id, first_name=first_name, last_name=last_name, about=about, username=username
        )
        return await temp.get_me(account.id)
    finally:
        await temp.disconnect_all()


async def update_avatar_standalone(account, photo_bytes: bytes) -> None:
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        await temp.update_avatar(account.id, photo_bytes)
    finally:
        await temp.disconnect_all()


async def post_story_standalone(account, media_bytes: bytes, filename: str, caption: str | None = None) -> None:
    temp = TelegramManager()
    await temp.connect_account(account)
    try:
        await temp.post_story(account.id, media_bytes, filename, caption)
    finally:
        await temp.disconnect_all()


manager = TelegramManager()
