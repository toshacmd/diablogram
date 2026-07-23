import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    LIMITED = "limited"  # temporary FloodWait, auto-recovers at limited_until
    BANNED = "banned"
    DISABLED = "disabled"  # manually turned off by the owner


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))

    # Encrypted Telethon StringSession.
    session_string_enc: Mapped[str] = mapped_column(Text)

    # Per-account proxy. proxy_type is one of: socks5, socks4, http.
    proxy_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    proxy_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    signature: Mapped[str] = mapped_column(Text, default="")

    persona_id: Mapped[int | None] = mapped_column(ForeignKey("personas.id", ondelete="SET NULL"), nullable=True)
    persona: Mapped["Persona"] = relationship(back_populates="accounts")

    daily_comment_cap: Mapped[int] = mapped_column(Integer, default=20)

    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), default=AccountStatus.ACTIVE)
    status_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    limited_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    assignments: Mapped[list["AccountChannelAssignment"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    comment_logs: Mapped[list["CommentLog"]] = relationship(back_populates="account")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Account {self.label!r} status={self.status}>"
