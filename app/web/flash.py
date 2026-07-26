"""Redirect-with-flash helper.

Flash text often embeds external strings (Telegram error messages, channel
titles) that may contain '&' or ';' — characters the query-string parser
treats as separators, silently truncating the message. quote() everything.
"""
from urllib.parse import quote

from fastapi.responses import RedirectResponse


def flash_redirect(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?flash={quote(message, safe='')}", status_code=303)
