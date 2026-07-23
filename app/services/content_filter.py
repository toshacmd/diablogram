"""Programmatic safety net applied to AI-generated comments before publishing.

Not human moderation — a fast, automatic block on obviously bad output
(profanity, banned topics). Can be toggled off entirely via GlobalSettings.
"""
from __future__ import annotations

import re


def check_text(text: str, stop_terms: list[str]) -> str | None:
    """Return the matched stop-term if `text` should be blocked, else None."""
    normalized = text.lower()
    for term in stop_terms:
        term = term.strip().lower()
        if not term:
            continue
        pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
        if re.search(pattern, normalized):
            return term
    return None
