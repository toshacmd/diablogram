import datetime as dt
import enum

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CommentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    POSTED = "posted"
    SKIPPED_FILTER = "skipped_filter"
    FAILED = "failed"


class CommentLog(Base):
    __tablename__ = "comment_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))

    source_post_id: Mapped[int] = mapped_column(BigInteger)
    comment_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    persona_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generated_text: Mapped[str] = mapped_column(Text)
    signature_text: Mapped[str] = mapped_column(Text)
    full_text: Mapped[str] = mapped_column(Text)

    status: Mapped[CommentStatus] = mapped_column(Enum(CommentStatus), default=CommentStatus.SCHEDULED)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    scheduled_for: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped["Account"] = relationship(back_populates="comment_logs")
    channel: Mapped["Channel"] = relationship(back_populates="comment_logs")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CommentLog account={self.account_id} channel={self.channel_id} status={self.status}>"
