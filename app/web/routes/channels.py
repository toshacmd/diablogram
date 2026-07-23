from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.db import async_session_factory
from app.models import Account, AccountChannelAssignment, Channel
from app.services.telegram_manager import resolve_channel_standalone
from app.web.templating import templates

router = APIRouter()


@router.get("/channels")
async def list_channels(request: Request):
    async with async_session_factory() as session:
        channels = (await session.execute(select(Channel).order_by(Channel.title))).scalars().all()
        counts = dict(
            (
                await session.execute(
                    select(AccountChannelAssignment.channel_id, func.count(AccountChannelAssignment.id)).group_by(
                        AccountChannelAssignment.channel_id
                    )
                )
            ).all()
        )
        accounts = (await session.execute(select(Account).order_by(Account.label))).scalars().all()

    return templates.TemplateResponse(
        request,
        "channels.html",
        {
            "active": "channels",
            "channels": channels,
            "assignment_counts": counts,
            "accounts": accounts,
        },
    )


@router.post("/channels/add")
async def add_channel(request: Request, account_id: int = Form(...), username_or_link: str = Form(...)):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/channels?flash=Аккаунт не найден", status_code=303)

        tg_channel_id, title, username = await resolve_channel_standalone(account, username_or_link.strip())

        existing = (
            await session.execute(select(Channel).where(Channel.tg_channel_id == tg_channel_id))
        ).scalar_one_or_none()
        if existing:
            existing.title = title
            existing.username = username
            existing.is_active = True
        else:
            session.add(Channel(tg_channel_id=tg_channel_id, title=title, username=username, is_active=True))
        await session.commit()

    return RedirectResponse(f"/channels?flash=Канал «{title}» добавлен", status_code=303)


@router.post("/channels/{channel_id}/toggle")
async def toggle_channel(channel_id: int):
    async with async_session_factory() as session:
        channel = await session.get(Channel, channel_id)
        if channel:
            channel.is_active = not channel.is_active
            await session.commit()
    return RedirectResponse("/channels", status_code=303)
