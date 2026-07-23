import datetime as dt

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.db import async_session_factory
from app.models import Account, AccountStatus, Channel, CommentLog, CommentStatus
from app.web.templating import templates

router = APIRouter()


@router.get("/")
async def dashboard(request: Request):
    async with async_session_factory() as session:
        total_channels = await session.scalar(select(func.count(Channel.id)).where(Channel.is_active == True))  # noqa: E712
        total_accounts = await session.scalar(select(func.count(Account.id)))
        by_status = dict(
            (await session.execute(select(Account.status, func.count(Account.id)).group_by(Account.status))).all()
        )

        today_start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        posted_today = await session.scalar(
            select(func.count(CommentLog.id)).where(
                CommentLog.status == CommentStatus.POSTED, CommentLog.posted_at >= today_start
            )
        )
        recent = (
            (
                await session.execute(
                    select(CommentLog).order_by(CommentLog.created_at.desc()).limit(15)
                )
            )
            .scalars()
            .all()
        )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "total_channels": total_channels or 0,
            "total_accounts": total_accounts or 0,
            "by_status": {s.value: c for s, c in by_status.items()},
            "posted_today": posted_today or 0,
            "recent": recent,
        },
    )
