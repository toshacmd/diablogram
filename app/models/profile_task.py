import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utc_now


class ProfileTaskKind(str, enum.Enum):
    AVATAR = "avatar"
    STORY = "story"
    BIO = "bio"


class ProfileTaskItemStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class ProfileTask(Base):
    """One bulk profile action (same avatar / story / bio for N accounts).

    Created by the web panel, executed by the worker in the background with
    pacing between accounts — doing Telegram round trips per account inside
    the HTTP request is the same 504/flood-wait trap as the old synchronous
    channel joins. Media lives on disk under data/profile_tasks (the shared
    ./data volume), not in the DB."""

    __tablename__ = "profile_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ProfileTaskKind] = mapped_column(Enum(ProfileTaskKind))

    # Avatar/story payload on disk; original filename kept because story
    # upload tells photo from video by extension.
    media_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Bio text, or the story caption.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    items: Mapped[list["ProfileTaskItem"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProfileTask {self.id} kind={self.kind}>"


class ProfileTaskItem(Base):
    """The task applied to one account. Stays `pending` through flood-waits
    (retried once the limit expires); `failed` is terminal — no automatic
    reanimation, matching the project's account-health philosophy."""

    __tablename__ = "profile_task_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("profile_tasks.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))

    status: Mapped[ProfileTaskItemStatus] = mapped_column(
        Enum(ProfileTaskItemStatus), default=ProfileTaskItemStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["ProfileTask"] = relationship(back_populates="items")
    account: Mapped["Account"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProfileTaskItem task={self.task_id} account={self.account_id} status={self.status}>"
