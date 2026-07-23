from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.crypto import encrypt
from app.db import async_session_factory
from app.models import Account, AccountChannelAssignment, AccountStatus, Channel, Persona
from app.services.exceptions import AccountBannedError, AccountLimitedError
from app.services.telegram_manager import join_channel_standalone
from app.web.templating import templates

router = APIRouter()


@router.get("/accounts")
async def list_accounts(request: Request, channel_id: int | None = None):
    async with async_session_factory() as session:
        accounts = (
            (
                await session.execute(
                    select(Account).options(joinedload(Account.persona)).order_by(Account.label)
                )
            )
            .scalars()
            .all()
        )
        personas = (await session.execute(select(Persona).order_by(Persona.name))).scalars().all()

        assigned_channel_ids: dict[int, set[int]] = {}
        for a in accounts:
            rows = (
                await session.execute(
                    select(AccountChannelAssignment.channel_id).where(AccountChannelAssignment.account_id == a.id)
                )
            ).scalars().all()
            assigned_channel_ids[a.id] = set(rows)

        filter_channel = None
        if channel_id is not None:
            filter_channel = await session.get(Channel, channel_id)
            accounts = [a for a in accounts if channel_id in assigned_channel_ids.get(a.id, set())]

    return templates.TemplateResponse(
        request,
        "accounts.html",
        {
            "active": "accounts",
            "accounts": accounts,
            "personas": personas,
            "assigned_channel_ids": assigned_channel_ids,
            "filter_channel": filter_channel,
        },
    )


@router.post("/accounts/add")
async def add_account(
    request: Request,
    label: str = Form(...),
    session_string: str = Form(...),
    proxy_type: str = Form(""),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
    persona_id: str = Form(""),
    daily_comment_cap: int = Form(20),
    signature: str = Form(""),
):
    async with async_session_factory() as session:
        account = Account(
            label=label.strip(),
            session_string_enc=encrypt(session_string.strip()),
            proxy_type=proxy_type or None,
            proxy_host=proxy_host or None,
            proxy_port=int(proxy_port) if proxy_port else None,
            proxy_username=proxy_username or None,
            proxy_password_enc=encrypt(proxy_password) if proxy_password else None,
            persona_id=int(persona_id) if persona_id else None,
            daily_comment_cap=daily_comment_cap,
            signature=signature,
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        await session.commit()
    return RedirectResponse("/accounts?flash=Аккаунт добавлен", status_code=303)


@router.get("/accounts/{account_id}")
async def account_detail(request: Request, account_id: int):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        personas = (await session.execute(select(Persona).order_by(Persona.name))).scalars().all()
        channels = (await session.execute(select(Channel).order_by(Channel.title))).scalars().all()
        assigned = set(
            (
                await session.execute(
                    select(AccountChannelAssignment.channel_id).where(
                        AccountChannelAssignment.account_id == account_id
                    )
                )
            )
            .scalars()
            .all()
        )

    return templates.TemplateResponse(
        request,
        "account_detail.html",
        {
            "active": "accounts",
            "account": account,
            "personas": personas,
            "channels": channels,
            "assigned": assigned,
        },
    )


@router.post("/accounts/{account_id}/update")
async def update_account(
    account_id: int,
    label: str = Form(...),
    signature: str = Form(""),
    persona_id: str = Form(""),
    daily_comment_cap: int = Form(20),
    proxy_type: str = Form(""),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        account.label = label.strip()
        account.signature = signature
        account.persona_id = int(persona_id) if persona_id else None
        account.daily_comment_cap = daily_comment_cap
        account.proxy_type = proxy_type or None
        account.proxy_host = proxy_host or None
        account.proxy_port = int(proxy_port) if proxy_port else None
        account.proxy_username = proxy_username or None
        if proxy_password:
            account.proxy_password_enc = encrypt(proxy_password)
        await session.commit()
    return RedirectResponse(f"/accounts/{account_id}?flash=Сохранено", status_code=303)


@router.post("/accounts/{account_id}/update-session")
async def update_session(account_id: int, session_string: str = Form(...)):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        account.session_string_enc = encrypt(session_string.strip())
        account.status = AccountStatus.ACTIVE
        account.status_note = None
        account.limited_until = None
        await session.commit()
    return RedirectResponse(f"/accounts/{account_id}?flash=Сессия обновлена, статус сброшен на «активен»", status_code=303)


@router.post("/accounts/{account_id}/toggle-disabled")
async def toggle_disabled(account_id: int):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        if account.status == AccountStatus.DISABLED:
            account.status = AccountStatus.ACTIVE
        elif account.status == AccountStatus.ACTIVE:
            account.status = AccountStatus.DISABLED
        await session.commit()
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/assignments")
async def update_assignments(request: Request, account_id: int):
    form = await request.form()
    channel_ids = {int(v) for v in form.getlist("channel_ids")}

    join_errors: list[str] = []

    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)

        current = (
            (
                await session.execute(
                    select(AccountChannelAssignment).where(AccountChannelAssignment.account_id == account_id)
                )
            )
            .scalars()
            .all()
        )
        current_ids = {row.channel_id for row in current}
        new_ids = channel_ids - current_ids

        for row in current:
            if row.channel_id not in channel_ids:
                await session.delete(row)
        for cid in new_ids:
            session.add(AccountChannelAssignment(account_id=account_id, channel_id=cid))

        await session.commit()

        # Make sure the account can actually see/comment in each newly assigned
        # channel — join it if not already a member.
        for cid in new_ids:
            channel = await session.get(Channel, cid)
            if channel is None:
                continue
            target = channel.username or channel.tg_channel_id
            try:
                await join_channel_standalone(account, target, invite_link=channel.invite_link)
            except (AccountLimitedError, AccountBannedError, Exception) as e:  # noqa: BLE001
                join_errors.append(f"{channel.title}: {e}")

    flash = "Каналы обновлены"
    if join_errors:
        flash += f". Не удалось вступить в некоторые каналы: {'; '.join(join_errors)}"
    return RedirectResponse(f"/accounts/{account_id}?flash={flash}", status_code=303)


@router.post("/accounts/signatures/bulk")
async def bulk_signature(request: Request):
    form = await request.form()
    text = form.get("signature", "")
    apply_all = form.get("apply_all") == "on"
    account_ids = {int(v) for v in form.getlist("account_ids")}

    async with async_session_factory() as session:
        if apply_all:
            accounts = (await session.execute(select(Account))).scalars().all()
        else:
            accounts = (
                (await session.execute(select(Account).where(Account.id.in_(account_ids)))).scalars().all()
            )
        for a in accounts:
            a.signature = text
        await session.commit()
        count = len(accounts)

    return RedirectResponse(f"/accounts?flash=Подпись обновлена у {count} аккаунтов", status_code=303)
