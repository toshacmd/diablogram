import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utc_now


class ChannelBan(Base):
    """Records that an account got banned/restricted from writing in one
    specific channel (e.g. by a moderator) — kept even after the assignment
    is auto-removed, so it stays visible in the panel."""

    __tablename__ = "channel_bans"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    account: Mapped["Account"] = relationship()
    channel: Mapped["Channel"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChannelBan account={self.account_id} channel={self.channel_id}>"
