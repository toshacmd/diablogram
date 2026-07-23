from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AccountChannelAssignment(Base):
    """Many-to-many link: which accounts are allowed to comment on which channels."""

    __tablename__ = "account_channel_assignments"
    __table_args__ = (UniqueConstraint("account_id", "channel_id", name="uq_account_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))

    account: Mapped["Account"] = relationship(back_populates="assignments")
    channel: Mapped["Channel"] = relationship(back_populates="assignments")
