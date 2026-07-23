from sqlalchemy import Boolean, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GlobalSettings(Base):
    """Singleton settings row (always id=1)."""

    __tablename__ = "global_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    commenters_min: Mapped[int] = mapped_column(Integer, default=1)
    commenters_max: Mapped[int] = mapped_column(Integer, default=2)

    delay_min_seconds: Mapped[int] = mapped_column(Integer, default=120)
    delay_max_seconds: Mapped[int] = mapped_column(Integer, default=1800)

    content_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_terms: Mapped[list[str]] = mapped_column(JSON, default=list)

    def __repr__(self) -> str:  # pragma: no cover
        return "<GlobalSettings>"
