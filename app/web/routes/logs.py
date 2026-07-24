import math

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.db import async_session_factory
from app.models import Channel, CommentLog, CommentStatus
from app.web.templating import templates

router = APIRouter()

PAGE_SIZE = 50


@router.get("/logs")
async def list_logs(
    request: Request,
    page: int = 1,
    status: str = "",
    channel_id: int | None = None,
):
    page = max(1, page)

    # Фильтры приходят из GET-формы; неизвестные значения просто игнорируем.
    conditions = []
    if status in {s.value for s in CommentStatus}:
        conditions.append(CommentLog.status == CommentStatus(status))
    else:
        status = ""
    if channel_id:
        conditions.append(CommentLog.channel_id == channel_id)

    async with async_session_factory() as session:
        total = await session.scalar(select(func.count(CommentLog.id)).where(*conditions)) or 0
        total_pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(page, total_pages)

        logs = (
            (
                await session.execute(
                    select(CommentLog)
                    .options(joinedload(CommentLog.account), joinedload(CommentLog.channel))
                    .where(*conditions)
                    .order_by(CommentLog.created_at.desc())
                    .offset((page - 1) * PAGE_SIZE)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
        channels = (await session.execute(select(Channel).order_by(Channel.title))).scalars().all()

    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "active": "logs",
            "logs": logs,
            "channels": channels,
            "page": page,
            "total": total,
            "total_pages": total_pages,
            "page_size": PAGE_SIZE,
            "f_status": status,
            "f_channel_id": channel_id,
            "has_filters": bool(status or channel_id),
        },
    )
