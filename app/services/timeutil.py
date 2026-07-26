"""Datetime helpers shared by the worker services."""
from __future__ import annotations

import datetime as dt


def ensure_aware(value: dt.datetime | None) -> dt.datetime | None:
    """Values stored via asyncpg come back timezone-aware, but SQLite (dev)
    returns them naive — normalize to aware UTC so comparisons with
    datetime.now(timezone.utc) never raise."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value
