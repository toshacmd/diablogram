class AccountLimitedError(Exception):
    """Raised when Telegram temporarily rate-limits an account (FloodWait)."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Account rate-limited for {retry_after_seconds}s")


class AccountBannedError(Exception):
    """Raised when an account itself is banned / deactivated / can no longer
    act at all — e.g. UserDeactivatedBanError, AuthKeyUnregisteredError."""


class ChannelBannedError(Exception):
    """Raised when an account is banned/restricted from writing in one
    specific channel/chat (e.g. by a moderator) — the account itself is
    otherwise fine and can still be used elsewhere."""
