import datetime as dt

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utc_now


class Proxy(Base):
    """A reusable proxy entry the owner picks from when adding accounts.

    Catalog semantics: assigning a proxy to an account COPIES its values into
    the account's own proxy_* columns (the operational source the Telethon
    client is built from) and records provenance via Account.proxy_id.
    Editing a catalog entry later does not silently re-route accounts that
    already copied it — re-apply it on the account to pick up changes.
    """

    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    proxy_type: Mapped[str] = mapped_column(String(20))  # socks5 | socks4 | http
    proxy_host: Mapped[str] = mapped_column(String(255))
    proxy_port: Mapped[int] = mapped_column(Integer)
    proxy_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    def display_name(self) -> str:
        return self.label or f"{self.proxy_type} · {self.proxy_host}:{self.proxy_port}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Proxy {self.proxy_type}://{self.proxy_host}:{self.proxy_port}>"
