from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.db import async_session_factory
from app.models import CommentLog
from app.web.templating import templates

router = APIRouter()

PAGE_SIZE = 50


@router.get("/logs")
async def list_logs(request: Request, page: int = 1):
    page = max(1, page)
    async with async_session_factory() as session:
        logs = (
            (
                await session.execute(
                    select(CommentLog)
                    .options(joinedload(CommentLog.account), joinedload(CommentLog.channel))
                    .order_by(CommentLog.created_at.desc())
                    .offset((page - 1) * PAGE_SIZE)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        request, "logs.html", {"active": "logs", "logs": logs, "page": page}
    )
