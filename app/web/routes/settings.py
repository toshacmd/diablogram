from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.db import async_session_factory
from app.models import GlobalSettings
from app.web.templating import templates

router = APIRouter()


async def _get_or_create(session) -> GlobalSettings:
    row = (await session.execute(select(GlobalSettings).where(GlobalSettings.id == 1))).scalar_one_or_none()
    if row is None:
        row = GlobalSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("/settings")
async def get_settings_page(request: Request):
    async with async_session_factory() as session:
        settings_row = await _get_or_create(session)
    return templates.TemplateResponse(
        request, "settings.html", {"active": "settings", "s": settings_row}
    )


@router.post("/settings")
async def update_settings(
    commenters_min: int = Form(...),
    commenters_max: int = Form(...),
    delay_min_seconds: int = Form(...),
    delay_max_seconds: int = Form(...),
    content_filter_enabled: str = Form(""),
    stop_terms: str = Form(""),
):
    async with async_session_factory() as session:
        row = await _get_or_create(session)
        row.commenters_min = max(1, commenters_min)
        row.commenters_max = max(row.commenters_min, commenters_max)
        row.delay_min_seconds = max(0, delay_min_seconds)
        row.delay_max_seconds = max(row.delay_min_seconds, delay_max_seconds)
        row.content_filter_enabled = content_filter_enabled == "on"
        row.stop_terms = [t.strip() for t in stop_terms.splitlines() if t.strip()]
        await session.commit()
    return RedirectResponse("/settings?flash=Настройки сохранены", status_code=303)
