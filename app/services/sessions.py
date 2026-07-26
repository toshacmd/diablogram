"""Converting uploaded Telethon .session files (SQLite) into the
StringSession form this app stores (encrypted) — lets the owner bulk-import
accounts straight from a folder of session files.
"""
from __future__ import annotations

import os
import tempfile

from telethon.sessions import SQLiteSession, StringSession


def session_file_to_string(data: bytes) -> str:
    """Returns the StringSession equivalent of a .session file's contents.

    Raises ValueError for files that aren't a usable session (not SQLite,
    old/foreign schema, or no auth key inside). Purely local — no Telegram
    round trip, so whether the session is still *authorized* is only found
    out later, when the worker first connects the account.
    """
    fd, path = tempfile.mkstemp(suffix=".session")
    os.close(fd)
    session = None
    try:
        with open(path, "wb") as f:
            f.write(data)
        try:
            session = SQLiteSession(path)
        except Exception as e:  # noqa: BLE001 — sqlite3 errors, schema mismatches
            raise ValueError(f"не похоже на файл сессии Telethon ({e})") from e
        if session.auth_key is None or not getattr(session.auth_key, "key", None):
            raise ValueError("в файле нет ключа авторизации — сессия пустая")
        return StringSession.save(session)
    finally:
        if session is not None:
            session.close()
        try:
            os.unlink(path)
        except OSError:
            pass
