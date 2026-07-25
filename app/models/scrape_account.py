import datetime as dt

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.account import AccountStatus


class ScrapeAccount(Base):
    """A Telegram account used only for lead-gen channel discovery (search +
    similar-channel expansion) — never comments, never joins the comment pool.
    Kept as a separate entity from Account so a burned scrape account can't
    collide with (or ever be confused for) a revenue-producing one."""

    __tablename__ = "scrape_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    session_string_enc: Mapped[str] = mapped_column(Text)

    proxy_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    proxy_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Auto-detected from client.get_me().premium on connect — informational,
    # affects how many similar-channel recommendations Telegram returns.
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), default=AccountStatus.ACTIVE)
    status_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    limited_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ScrapeAccount {self.label!r} status={self.status}>"
