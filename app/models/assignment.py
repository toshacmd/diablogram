import enum

from sqlalchemy import Enum, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class JoinStatus(str, enum.Enum):
    PENDING = "pending"
    JOINED = "joined"
    PENDING_APPROVAL = "pending_approval"
    FAILED = "failed"


class AccountChannelAssignment(Base):
    """Many-to-many link: which accounts are allowed to comment on which channels."""

    __tablename__ = "account_channel_assignments"
    __table_args__ = (UniqueConstraint("account_id", "channel_id", name="uq_account_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))

    # Joining the channel (+ its discussion group) happens in the background
    # in the worker (app.services.sync.process_pending_joins), not
    # synchronously on save — a large "select all channels" batch was slow
    # enough per-channel to trip nginx's gateway timeout when done inline.
    join_status: Mapped[JoinStatus] = mapped_column(Enum(JoinStatus), default=JoinStatus.PENDING)
    join_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    account: Mapped["Account"] = relationship(back_populates="assignments")
    channel: Mapped["Channel"] = relationship(back_populates="assignments")
