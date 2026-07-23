class AccountLimitedError(Exception):
    """Raised when Telegram temporarily rate-limits an account (FloodWait)."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Account rate-limited for {retry_after_seconds}s")


class AccountBannedError(Exception):
    """Raised when an account is banned / deactivated / can no longer act."""
