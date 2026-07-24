import datetime as dt

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.db import async_session_factory
from app.models import Account, AccountStatus, Channel, CommentLog, CommentStatus
from app.web.templating import templates

router = APIRouter()

# Сколько дней показывать на столбчатом графике активности.
ACTIVITY_DAYS = 14


@router.get("/")
async def dashboard(request: Request):
    today_start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today_start - dt.timedelta(days=ACTIVITY_DAYS - 1)

    async with async_session_factory() as session:
        total_channels = await session.scalar(select(func.count(Channel.id)).where(Channel.is_active == True))  # noqa: E712
        all_channels = await session.scalar(select(func.count(Channel.id)))
        total_accounts = await session.scalar(select(func.count(Account.id)))
        by_status = dict(
            (await session.execute(select(Account.status, func.count(Account.id)).group_by(Account.status))).all()
        )

        posted_today = await session.scalar(
            select(func.count(CommentLog.id)).where(
                CommentLog.status == CommentStatus.POSTED, CommentLog.posted_at >= today_start
            )
        )
        failed_today = await session.scalar(
            select(func.count(CommentLog.id)).where(
                CommentLog.status == CommentStatus.FAILED, CommentLog.created_at >= today_start
            )
        )
        scheduled_pending = await session.scalar(
            select(func.count(CommentLog.id)).where(CommentLog.status == CommentStatus.SCHEDULED)
        )

        # Публикации по дням — для графика активности.
        day_col = func.date(CommentLog.posted_at)
        day_rows = (
            await session.execute(
                select(day_col.label("day"), func.count(CommentLog.id))
                .where(CommentLog.status == CommentStatus.POSTED, CommentLog.posted_at >= window_start)
                .group_by(day_col)
            )
        ).all()

        # joinedload — иначе доступ к log.channel/log.account в шаблоне уронит
        # рендер MissingGreenlet: сессия к тому моменту уже закрыта.
        recent = (
            (
                await session.execute(
                    select(CommentLog)
                    .options(joinedload(CommentLog.account), joinedload(CommentLog.channel))
                    .order_by(CommentLog.created_at.desc())
                    .limit(12)
                )
            )
            .scalars()
            .all()
        )

    # Драйвер может вернуть date или строку 'YYYY-MM-DD' — приводим к одному виду.
    per_day = {(d if isinstance(d, str) else d.isoformat()): c for d, c in day_rows}

    activity = []
    for offset in range(ACTIVITY_DAYS - 1, -1, -1):
        day = (today_start - dt.timedelta(days=offset)).date()
        activity.append(
            {
                "label": day.strftime("%d.%m"),
                "tick": day.strftime("%d"),
                "count": per_day.get(day.isoformat(), 0),
                "is_today": offset == 0,
            }
        )
    activity_max = max((d["count"] for d in activity), default=0)

    status_counts = {s.value: c for s, c in by_status.items()}

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "total_channels": total_channels or 0,
            "all_channels": all_channels or 0,
            "total_accounts": total_accounts or 0,
            "by_status": status_counts,
            "active_accounts": status_counts.get(AccountStatus.ACTIVE.value, 0),
            "posted_today": posted_today or 0,
            "failed_today": failed_today or 0,
            "scheduled_pending": scheduled_pending or 0,
            "activity": activity,
            "activity_max": activity_max,
            "activity_total": sum(d["count"] for d in activity),
            "activity_days": ACTIVITY_DAYS,
            "recent": recent,
        },
    )
