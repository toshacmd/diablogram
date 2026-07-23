import datetime as dt

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Persona(Base):
    """A tone/personality preset. Built-in presets ship with the app; users can add
    their own custom personas the same way — the only difference is `is_builtin`.
    """

    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    prompt_text: Mapped[str] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    accounts: Mapped[list["Account"]] = relationship(back_populates="persona")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Persona {self.name!r}>"
