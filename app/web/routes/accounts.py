import datetime as dt
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from telethon.errors import UsernameInvalidError, UsernameNotModifiedError, UsernameOccupiedError

from app.crypto import encrypt
from app.db import async_session_factory
from app.models import Account, AccountChannelAssignment, AccountStatus, Channel, ChannelBan, CommentLog, Persona
from app.services.exceptions import AccountBannedError, AccountLimitedError, JoinRequestPendingError
from app.services.telegram_manager import (
    join_channel_standalone,
    post_story_standalone,
    sync_profile_standalone,
    update_avatar_standalone,
    update_profile_standalone,
)
from app.web.templating import templates

router = APIRouter()

# Cached Telegram avatars — runtime data, not part of the repo (see .gitignore).
AVATAR_DIR = Path("data/avatars")


def _avatar_path(account_id: int) -> Path:
    return AVATAR_DIR / f"{account_id}.jpg"


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
        channel_bans = (
            (
                await session.execute(
                    select(ChannelBan)
                    .options(joinedload(ChannelBan.channel))
                    .where(ChannelBan.account_id == account_id)
                    .order_by(ChannelBan.created_at.desc())
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
            "channel_bans": channel_bans,
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


@router.post("/accounts/{account_id}/delete")
async def delete_account(account_id: int):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        label = account.label
        # Cleared explicitly rather than relying on DB-level ON DELETE CASCADE
        # alone — SQLite (used in dev/tests) doesn't enforce FKs by default,
        # so this keeps behavior identical across SQLite and Postgres.
        await session.execute(sa_delete(CommentLog).where(CommentLog.account_id == account_id))
        await session.execute(sa_delete(ChannelBan).where(ChannelBan.account_id == account_id))
        await session.delete(account)
        await session.commit()
    return RedirectResponse(f"/accounts?flash=Аккаунт «{label}» удалён", status_code=303)


@router.post("/accounts/{account_id}/assignments")
async def update_assignments(request: Request, account_id: int):
    form = await request.form()
    channel_ids = {int(v) for v in form.getlist("channel_ids")}

    join_errors: list[str] = []
    pending_requests: list[str] = []

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
            except JoinRequestPendingError:
                pending_requests.append(channel.title)
            except (AccountLimitedError, AccountBannedError, Exception) as e:  # noqa: BLE001
                join_errors.append(f"{channel.title}: {e}")

    flash = "Каналы обновлены"
    if pending_requests:
        flash += f". Заявка на вступление отправлена, ждёт одобрения администратора: {', '.join(pending_requests)}"
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


@router.get("/accounts/{account_id}/avatar")
async def account_avatar(account_id: int):
    path = _avatar_path(account_id)
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@router.post("/accounts/{account_id}/sync-profile")
async def sync_profile(account_id: int):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)

        try:
            me, avatar_bytes = await sync_profile_standalone(account)
        except AccountLimitedError as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Аккаунт временно ограничен Telegram ({e.retry_after_seconds} сек)",
                status_code=303,
            )
        except AccountBannedError as e:
            return RedirectResponse(f"/accounts/{account_id}?flash=Аккаунт забанен: {e}", status_code=303)
        except Exception as e:  # noqa: BLE001
            return RedirectResponse(f"/accounts/{account_id}?flash=Не удалось подключиться: {e}", status_code=303)

        account.tg_user_id = me.id
        account.tg_username = me.username
        account.tg_first_name = me.first_name
        account.tg_last_name = me.last_name
        account.tg_synced_at = dt.datetime.utcnow()
        if avatar_bytes:
            AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            _avatar_path(account_id).write_bytes(avatar_bytes)
        await session.commit()

    return RedirectResponse(f"/accounts/{account_id}?flash=Профиль обновлён из Telegram", status_code=303)


@router.post("/accounts/{account_id}/update-profile")
async def update_profile(
    account_id: int,
    tg_first_name: str = Form(""),
    tg_last_name: str = Form(""),
    tg_username: str = Form(""),
    tg_bio: str = Form(""),
):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)

        first_name = tg_first_name.strip()
        last_name = tg_last_name.strip()
        username = tg_username.strip().lstrip("@")
        bio = tg_bio.strip()

        kwargs: dict = {}
        if first_name != (account.tg_first_name or ""):
            kwargs["first_name"] = first_name
        if last_name != (account.tg_last_name or ""):
            kwargs["last_name"] = last_name
        if bio != (account.tg_bio or ""):
            kwargs["about"] = bio
        if username != (account.tg_username or ""):
            kwargs["username"] = username

        if not kwargs:
            return RedirectResponse(f"/accounts/{account_id}?flash=Изменений нет", status_code=303)

        try:
            me = await update_profile_standalone(account, **kwargs)
        except (UsernameOccupiedError, UsernameInvalidError, UsernameNotModifiedError) as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Не удалось изменить юзернейм: {e}", status_code=303
            )
        except AccountLimitedError as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Аккаунт временно ограничен Telegram ({e.retry_after_seconds} сек)",
                status_code=303,
            )
        except AccountBannedError as e:
            return RedirectResponse(f"/accounts/{account_id}?flash=Аккаунт забанен: {e}", status_code=303)
        except Exception as e:  # noqa: BLE001
            return RedirectResponse(f"/accounts/{account_id}?flash=Не удалось подключиться: {e}", status_code=303)

        account.tg_user_id = me.id
        account.tg_username = me.username
        account.tg_first_name = me.first_name
        account.tg_last_name = me.last_name
        account.tg_bio = bio
        account.tg_synced_at = dt.datetime.utcnow()
        await session.commit()

    return RedirectResponse(f"/accounts/{account_id}?flash=Профиль в Telegram обновлён", status_code=303)


@router.post("/accounts/{account_id}/update-avatar")
async def update_avatar(account_id: int, avatar_file: UploadFile = File(...)):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)

        if not (avatar_file.content_type or "").startswith("image/"):
            return RedirectResponse(f"/accounts/{account_id}?flash=Нужен файл изображения", status_code=303)
        photo_bytes = await avatar_file.read()
        if len(photo_bytes) > 10 * 1024 * 1024:
            return RedirectResponse(f"/accounts/{account_id}?flash=Файл слишком большой (макс. 10 МБ)", status_code=303)

        try:
            await update_avatar_standalone(account, photo_bytes)
        except AccountLimitedError as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Аккаунт временно ограничен Telegram ({e.retry_after_seconds} сек)",
                status_code=303,
            )
        except AccountBannedError as e:
            return RedirectResponse(f"/accounts/{account_id}?flash=Аккаунт забанен: {e}", status_code=303)
        except Exception as e:  # noqa: BLE001
            return RedirectResponse(f"/accounts/{account_id}?flash=Не удалось подключиться: {e}", status_code=303)

        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        _avatar_path(account_id).write_bytes(photo_bytes)
        account.tg_synced_at = dt.datetime.utcnow()
        await session.commit()

    return RedirectResponse(f"/accounts/{account_id}?flash=Аватар обновлён", status_code=303)


@router.post("/accounts/{account_id}/story")
async def post_story(account_id: int, story_file: UploadFile = File(...), caption: str = Form("")):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)

        content_type = story_file.content_type or ""
        if not (content_type.startswith("image/") or content_type.startswith("video/")):
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Нужен файл изображения или видео", status_code=303
            )
        media_bytes = await story_file.read()
        if len(media_bytes) > 50 * 1024 * 1024:
            return RedirectResponse(f"/accounts/{account_id}?flash=Файл слишком большой (макс. 50 МБ)", status_code=303)

        try:
            await post_story_standalone(account, media_bytes, story_file.filename or "story.jpg", caption.strip() or None)
        except AccountLimitedError as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Аккаунт временно ограничен Telegram ({e.retry_after_seconds} сек)",
                status_code=303,
            )
        except AccountBannedError as e:
            return RedirectResponse(f"/accounts/{account_id}?flash=Аккаунт забанен: {e}", status_code=303)
        except Exception as e:  # noqa: BLE001
            return RedirectResponse(f"/accounts/{account_id}?flash=Не удалось опубликовать сторис: {e}", status_code=303)

    return RedirectResponse(f"/accounts/{account_id}?flash=Сторис опубликована", status_code=303)
