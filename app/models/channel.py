import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Telegram numeric channel id (as seen by Telethon), and human-friendly handle/title.
    tg_channel_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255))

    # Invite link (t.me/+... or t.me/joinchat/...), needed to join accounts to
    # private channels that have no public @username — a bare numeric id can't
    # be resolved by an account that has never seen the channel before.
    invite_link: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    assignments: Mapped[list["AccountChannelAssignment"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    comment_logs: Mapped[list["CommentLog"]] = relationship(back_populates="channel")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Channel {self.title!r}>"
