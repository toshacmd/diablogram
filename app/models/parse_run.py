import datetime as dt
import enum

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ParseRunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ParseRun(Base):
    """One lead-gen parsing job: keyword search + recursive similar-channel
    expansion, filtered down to RU channels with open comments. Executed by
    the worker process, not the web process — see app/services/parser.py."""

    __tablename__ = "parse_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    keywords: Mapped[str] = mapped_column(Text)
    min_subscribers: Mapped[int] = mapped_column(Integer, default=1000)
    max_inactive_days: Mapped[int] = mapped_column(Integer, default=14)
    depth: Mapped[int] = mapped_column(Integer, default=1)

    status: Mapped[ParseRunStatus] = mapped_column(Enum(ParseRunStatus), default=ParseRunStatus.QUEUED)
    status_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    channels_found: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channels: Mapped[list["ParsedChannel"]] = relationship(back_populates="run", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ParseRun {self.id} status={self.status}>"


class ParsedChannel(Base):
    """One channel found by a ParseRun. Scoped to a single run — no
    cross-run dedup/status tracking for v1 (raw list per run, by design)."""

    __tablename__ = "parsed_channels"
    __table_args__ = (UniqueConstraint("run_id", "tg_channel_id", name="uq_parsed_channel_run"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("parse_runs.id", ondelete="CASCADE"))
    run: Mapped["ParseRun"] = relationship(back_populates="channels")

    tg_channel_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(64))
    subscriber_count: Mapped[int] = mapped_column(Integer)
    found_via: Mapped[str] = mapped_column(String(20))  # 'keyword' | 'similar'

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ParsedChannel {self.title!r} @{self.username}>"
