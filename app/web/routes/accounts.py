import datetime as dt
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload
from telethon.errors import UsernameInvalidError, UsernameNotModifiedError, UsernameOccupiedError

from app.crypto import decrypt, encrypt
from app.db import async_session_factory
from app.models import (
    Account,
    AccountChannelAssignment,
    AccountStatus,
    Channel,
    ChannelBan,
    CommentLog,
    JoinStatus,
    Persona,
    ProfileTask,
    ProfileTaskItem,
    ProfileTaskItemStatus,
    ProfileTaskKind,
    Proxy,
)
from app.services.exceptions import AccountBannedError, AccountLimitedError
from app.services.images import fit_avatar_to_square
from app.services.profile_tasks import MEDIA_DIR
from app.services.sessions import session_file_to_string
from app.services.telegram_manager import (
    post_story_standalone,
    sync_profile_standalone,
    update_avatar_standalone,
    update_profile_standalone,
)
from app.web.flash import flash_redirect
from app.web.templating import templates

router = APIRouter()

# Cached Telegram avatars — runtime data, not part of the repo (see .gitignore).
AVATAR_DIR = Path("data/avatars")


def _avatar_path(account_id: int) -> Path:
    return AVATAR_DIR / f"{account_id}.jpg"


def _parse_proxy_port(raw: str) -> int | None:
    """'' -> None; digits -> int; anything else -> ValueError (a typo here
    used to surface as a bare 500)."""
    raw = raw.strip()
    if not raw:
        return None
    return int(raw)


def _apply_catalog_proxy(account: Account, proxy: Proxy) -> None:
    """Copies a catalog proxy's values into the account's own (operational)
    proxy fields and records where they came from. Copy semantics are
    deliberate — see models/proxy.py."""
    account.proxy_id = proxy.id
    account.proxy_type = proxy.proxy_type
    account.proxy_host = proxy.proxy_host
    account.proxy_port = proxy.proxy_port
    account.proxy_username = proxy.proxy_username
    account.proxy_password_enc = proxy.proxy_password_enc


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

        proxies = (await session.execute(select(Proxy).order_by(Proxy.id))).scalars().all()

        # Последние массовые задачи профиля + их статистика и ошибки.
        tasks = (
            (await session.execute(select(ProfileTask).order_by(ProfileTask.created_at.desc()).limit(10)))
            .scalars()
            .all()
        )
        task_stats: dict[int, dict[str, int]] = {}
        task_failures: dict[int, list[ProfileTaskItem]] = {}
        if tasks:
            task_ids = [t.id for t in tasks]
            rows = (
                await session.execute(
                    select(ProfileTaskItem.task_id, ProfileTaskItem.status, func.count(ProfileTaskItem.id))
                    .where(ProfileTaskItem.task_id.in_(task_ids))
                    .group_by(ProfileTaskItem.task_id, ProfileTaskItem.status)
                )
            ).all()
            for task_id, item_status, count in rows:
                task_stats.setdefault(task_id, {})[item_status.value] = count
            failed_items = (
                (
                    await session.execute(
                        select(ProfileTaskItem)
                        .options(joinedload(ProfileTaskItem.account))
                        .where(
                            ProfileTaskItem.task_id.in_(task_ids),
                            ProfileTaskItem.status == ProfileTaskItemStatus.FAILED,
                        )
                        .order_by(ProfileTaskItem.id)
                    )
                )
                .scalars()
                .all()
            )
            for item in failed_items:
                task_failures.setdefault(item.task_id, []).append(item)

    return templates.TemplateResponse(
        request,
        "accounts.html",
        {
            "active": "accounts",
            "accounts": accounts,
            "personas": personas,
            "assigned_channel_ids": assigned_channel_ids,
            "filter_channel": filter_channel,
            "proxies": proxies,
            "profile_tasks": tasks,
            "task_stats": task_stats,
            "task_failures": task_failures,
        },
    )


@router.post("/accounts/add")
async def add_account(
    request: Request,
    label: str = Form(...),
    session_string: str = Form(...),
    proxy_id: str = Form(""),
    proxy_type: str = Form(""),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
    persona_id: str = Form(""),
    daily_comment_cap: int = Form(20),
    signature: str = Form(""),
):
    try:
        parsed_port = _parse_proxy_port(proxy_port)
    except ValueError:
        return RedirectResponse("/accounts?flash=Порт прокси должен быть числом", status_code=303)
    async with async_session_factory() as session:
        account = Account(
            label=label.strip(),
            session_string_enc=encrypt(session_string.strip()),
            proxy_type=proxy_type or None,
            proxy_host=proxy_host or None,
            proxy_port=parsed_port,
            proxy_username=proxy_username or None,
            proxy_password_enc=encrypt(proxy_password) if proxy_password else None,
            persona_id=int(persona_id) if persona_id else None,
            daily_comment_cap=daily_comment_cap,
            signature=signature,
            status=AccountStatus.ACTIVE,
        )
        # Прокси из справочника имеет приоритет над ручными полями.
        if proxy_id:
            proxy = await session.get(Proxy, int(proxy_id))
            if proxy is None:
                return RedirectResponse("/accounts?flash=Выбранный прокси не найден", status_code=303)
            _apply_catalog_proxy(account, proxy)
        session.add(account)
        await session.commit()
    return RedirectResponse("/accounts?flash=Аккаунт добавлен", status_code=303)


@router.post("/accounts/add-bulk-sessions")
async def add_accounts_bulk(
    request: Request,
    session_files: list[UploadFile] = File(...),
    proxy_id: str = Form(""),
    persona_id: str = Form(""),
    daily_comment_cap: int = Form(20),
):
    """Массовое добавление: пачка .session-файлов (Telethon SQLite),
    конвертируется в StringSession локально, без похода в Telegram. Живость
    сессий проверится позже — воркером, при первом подключении."""
    added: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    async with async_session_factory() as session:
        proxy = None
        if proxy_id:
            proxy = await session.get(Proxy, int(proxy_id))
            if proxy is None:
                return RedirectResponse("/accounts?flash=Выбранный прокси не найден", status_code=303)

        # Для дедупликации: строки сессий уже существующих аккаунтов.
        existing = {}
        for a in (await session.execute(select(Account))).scalars().all():
            try:
                existing[decrypt(a.session_string_enc)] = a.label
            except Exception:  # noqa: BLE001 — повреждённая запись не должна ломать импорт
                continue

        seen_in_batch: set[str] = set()
        for upload in session_files:
            name = upload.filename or "session"
            label = Path(name).stem or name
            data = await upload.read()
            if len(data) > 5 * 1024 * 1024:
                errors.append(f"{name}: файл слишком большой для .session")
                continue
            try:
                session_string = session_file_to_string(data)
            except ValueError as e:
                errors.append(f"{name}: {e}")
                continue

            if session_string in existing:
                skipped.append(f"{name} (уже есть: «{existing[session_string]}»)")
                continue
            if session_string in seen_in_batch:
                skipped.append(f"{name} (повтор в этой же пачке)")
                continue
            seen_in_batch.add(session_string)

            account = Account(
                label=label,
                session_string_enc=encrypt(session_string),
                persona_id=int(persona_id) if persona_id else None,
                daily_comment_cap=daily_comment_cap,
                signature="",
                status=AccountStatus.ACTIVE,
            )
            if proxy is not None:
                _apply_catalog_proxy(account, proxy)
            session.add(account)
            added.append(label)

        await session.commit()

    flash = f"Добавлено аккаунтов: {len(added)}"
    if skipped:
        flash += f". Пропущены: {', '.join(skipped)}"
    if errors:
        flash += f". Ошибки: {'; '.join(errors)}"
    flash += ". Статус сессий проверится воркером при первом подключении"
    return flash_redirect("/accounts", flash)


@router.get("/accounts/{account_id}")
async def account_detail(request: Request, account_id: int):
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        personas = (await session.execute(select(Persona).order_by(Persona.name))).scalars().all()
        channels = (await session.execute(select(Channel).order_by(Channel.title))).scalars().all()
        proxies = (await session.execute(select(Proxy).order_by(Proxy.id))).scalars().all()
        assigned = {
            row.channel_id: row
            for row in (
                await session.execute(
                    select(AccountChannelAssignment).where(AccountChannelAssignment.account_id == account_id)
                )
            )
            .scalars()
            .all()
        }
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
            "proxies": proxies,
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
    proxy_id: str = Form(""),
    proxy_type: str = Form(""),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
):
    try:
        parsed_port = _parse_proxy_port(proxy_port)
    except ValueError:
        return RedirectResponse(f"/accounts/{account_id}?flash=Порт прокси должен быть числом", status_code=303)
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        account.label = label.strip()
        account.signature = signature
        account.persona_id = int(persona_id) if persona_id else None
        account.daily_comment_cap = daily_comment_cap
        if proxy_id:
            # Прокси из справочника выбран в селекте — его значения имеют
            # приоритет и перезаписывают ручные поля.
            proxy = await session.get(Proxy, int(proxy_id))
            if proxy is None:
                return RedirectResponse(f"/accounts/{account_id}?flash=Выбранный прокси не найден", status_code=303)
            _apply_catalog_proxy(account, proxy)
        else:
            account.proxy_id = None
            account.proxy_type = proxy_type or None
            account.proxy_host = proxy_host or None
            account.proxy_port = parsed_port
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
    _avatar_path(account_id).unlink(missing_ok=True)  # don't leave orphaned cached avatars behind
    return flash_redirect("/accounts", f"Аккаунт «{label}» удалён")


@router.post("/accounts/{account_id}/assignments")
async def update_assignments(request: Request, account_id: int):
    form = await request.form()
    channel_ids = {int(v) for v in form.getlist("channel_ids")}

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
            session.add(AccountChannelAssignment(account_id=account_id, channel_id=cid, join_status=JoinStatus.PENDING))

        await session.commit()

    # Joining new channels (+ their discussion groups) happens in the
    # background — the worker picks up "pending" assignments on its next
    # sync cycle (see app.services.sync.process_pending_joins) and reuses the
    # connection it already keeps open for this account. Doing it here,
    # synchronously, per channel, was slow enough on a large "select all"
    # batch to trip nginx's gateway timeout.
    flash = "Каналы обновлены"
    if new_ids:
        flash += (
            ". Вступление в новые каналы выполнится в фоне (обычно в течение минуты) — "
            "статус видно в списке закреплённых каналов ниже"
        )
    return RedirectResponse(f"/accounts/{account_id}?flash={flash}", status_code=303)


@router.post("/accounts/{account_id}/assignments/cancel-pending")
async def cancel_pending_joins(account_id: int):
    """Unpins every channel this account hasn't joined yet (join_status =
    pending), stopping the background join queue for it. Escape hatch for
    when a big batch keeps tripping flood-waits: the owner cancels the rest
    and uses the account for commenting in the channels it did join.
    Re-selecting the channels later re-queues the joins."""
    async with async_session_factory() as session:
        account = await session.get(Account, account_id)
        if account is None:
            return RedirectResponse("/accounts?flash=Аккаунт не найден", status_code=303)
        result = await session.execute(
            sa_delete(AccountChannelAssignment).where(
                AccountChannelAssignment.account_id == account_id,
                AccountChannelAssignment.join_status == JoinStatus.PENDING,
            )
        )
        await session.commit()
        cancelled = result.rowcount or 0

    if not cancelled:
        return RedirectResponse(f"/accounts/{account_id}?flash=Вступлений в очереди нет", status_code=303)
    return flash_redirect(
        f"/accounts/{account_id}",
        f"Отменено вступлений: {cancelled}. Эти каналы откреплены от аккаунта — "
        "чтобы продолжить вступление позже, просто отметьте их снова и сохраните",
    )


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


@router.post("/accounts/profile-tasks/create")
async def create_profile_task(request: Request, media_file: UploadFile | None = File(None)):
    """Создаёт массовую задачу (аватар / сторис / био) для выбранных
    аккаунтов. Сами Telegram-запросы выполняет воркер в фоне с паузами —
    синхронно в HTTP-запросе на 50 аккаунтах это 504 и флуд-лимиты."""
    form = await request.form()
    kind_raw = form.get("kind", "")
    text = (form.get("text") or "").strip()
    apply_all = form.get("apply_all") == "on"
    ids_csv = form.get("account_ids_csv") or ""
    account_ids = {int(v) for v in ids_csv.split(",") if v.strip().isdigit()}

    try:
        kind = ProfileTaskKind(kind_raw)
    except ValueError:
        return RedirectResponse("/accounts?flash=Неизвестный тип массового действия", status_code=303)

    media_bytes: bytes | None = None
    if kind in (ProfileTaskKind.AVATAR, ProfileTaskKind.STORY):
        if media_file is None or not media_file.filename:
            return RedirectResponse("/accounts?flash=Для этого действия нужен файл", status_code=303)
        content_type = media_file.content_type or ""
        if kind == ProfileTaskKind.AVATAR and not content_type.startswith("image/"):
            return RedirectResponse("/accounts?flash=Для аватара нужен файл изображения", status_code=303)
        if kind == ProfileTaskKind.STORY and not (
            content_type.startswith("image/") or content_type.startswith("video/")
        ):
            return RedirectResponse("/accounts?flash=Для сторис нужно изображение или видео", status_code=303)
        media_bytes = await media_file.read()
        limit = 10 * 1024 * 1024 if kind == ProfileTaskKind.AVATAR else 50 * 1024 * 1024
        if len(media_bytes) > limit:
            return RedirectResponse(
                f"/accounts?flash=Файл слишком большой (макс. {limit // (1024 * 1024)} МБ)", status_code=303
            )
        if kind == ProfileTaskKind.AVATAR:
            try:
                media_bytes = fit_avatar_to_square(media_bytes)
            except Exception:  # noqa: BLE001
                return RedirectResponse(
                    "/accounts?flash=Не удалось обработать изображение — файл повреждён или формат не поддерживается",
                    status_code=303,
                )
    else:  # BIO
        if len(text) > 70:
            return RedirectResponse(
                "/accounts?flash=Био длиннее 70 символов — Telegram столько не примет", status_code=303
            )

    async with async_session_factory() as session:
        if apply_all:
            accounts = (await session.execute(select(Account))).scalars().all()
        else:
            if not account_ids:
                return RedirectResponse(
                    "/accounts?flash=Отметьте аккаунты в таблице или включите «всем аккаунтам»", status_code=303
                )
            accounts = (
                (await session.execute(select(Account).where(Account.id.in_(account_ids)))).scalars().all()
            )
        if not accounts:
            return RedirectResponse("/accounts?flash=Не найдено ни одного аккаунта", status_code=303)

        task = ProfileTask(kind=kind, text=text or None)
        session.add(task)
        await session.commit()
        await session.refresh(task)

        if media_bytes is not None:
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            if kind == ProfileTaskKind.AVATAR:
                filename = "avatar.jpg"  # уже перекодировано в квадратный JPEG
            else:
                # Расширение важно: по нему post_story отличает фото от видео.
                suffix = Path(media_file.filename or "").suffix[:16] or ".jpg"
                filename = f"story{suffix}"
            media_path = MEDIA_DIR / f"{task.id}_{filename}"
            media_path.write_bytes(media_bytes)
            task.media_path = str(media_path)
            task.media_filename = filename

        for account in accounts:
            session.add(ProfileTaskItem(task_id=task.id, account_id=account.id))
        await session.commit()

    kind_labels = {ProfileTaskKind.AVATAR: "аватар", ProfileTaskKind.STORY: "сторис", ProfileTaskKind.BIO: "био"}
    return flash_redirect(
        "/accounts",
        f"Задача «{kind_labels[kind]}» поставлена для {len(accounts)} аккаунтов — выполняется в фоне "
        "с паузами между аккаунтами, прогресс в блоке «Массовые задачи» ниже",
    )


@router.post("/accounts/profile-tasks/{task_id}/cancel")
async def cancel_profile_task(task_id: int):
    """Отменяет невыполненные позиции задачи (уже применённые не откатывает)."""
    async with async_session_factory() as session:
        task = await session.get(ProfileTask, task_id)
        if task is None:
            return RedirectResponse("/accounts?flash=Задача не найдена", status_code=303)
        result = await session.execute(
            sa_delete(ProfileTaskItem).where(
                ProfileTaskItem.task_id == task_id,
                ProfileTaskItem.status == ProfileTaskItemStatus.PENDING,
            )
        )
        await session.commit()
        cancelled = result.rowcount or 0
        remaining = await session.scalar(
            select(func.count(ProfileTaskItem.id)).where(
                ProfileTaskItem.task_id == task_id,
                ProfileTaskItem.status == ProfileTaskItemStatus.PENDING,
            )
        )
        if not remaining and task.media_path:
            Path(task.media_path).unlink(missing_ok=True)
    return RedirectResponse(f"/accounts?flash=Отменено позиций: {cancelled}", status_code=303)


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
            me, bio, avatar_bytes = await sync_profile_standalone(account)
        except AccountLimitedError as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Аккаунт временно ограничен Telegram ({e.retry_after_seconds} сек)",
                status_code=303,
            )
        except AccountBannedError as e:
            return flash_redirect(f"/accounts/{account_id}", f"Аккаунт забанен: {e}")
        except Exception as e:  # noqa: BLE001
            return flash_redirect(f"/accounts/{account_id}", f"Не удалось подключиться: {e}")

        account.tg_user_id = me.id
        account.tg_username = me.username
        account.tg_first_name = me.first_name
        account.tg_last_name = me.last_name
        account.tg_bio = bio
        account.tg_synced_at = dt.datetime.now(dt.timezone.utc)
        if avatar_bytes:
            AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            _avatar_path(account_id).write_bytes(avatar_bytes)
        else:
            # No avatar on Telegram (anymore) — drop the stale cached file too.
            _avatar_path(account_id).unlink(missing_ok=True)
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
            return flash_redirect(f"/accounts/{account_id}", f"Не удалось изменить юзернейм: {e}")
        except AccountLimitedError as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Аккаунт временно ограничен Telegram ({e.retry_after_seconds} сек)",
                status_code=303,
            )
        except AccountBannedError as e:
            return flash_redirect(f"/accounts/{account_id}", f"Аккаунт забанен: {e}")
        except Exception as e:  # noqa: BLE001
            return flash_redirect(f"/accounts/{account_id}", f"Не удалось подключиться: {e}")

        account.tg_user_id = me.id
        account.tg_username = me.username
        account.tg_first_name = me.first_name
        account.tg_last_name = me.last_name
        account.tg_bio = bio
        account.tg_synced_at = dt.datetime.now(dt.timezone.utc)
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

        # Telegram center-crops non-square avatars server-side (losing e.g.
        # a caption at the top of a tall promo image) — letterbox to square
        # before uploading so the whole image survives.
        try:
            photo_bytes = fit_avatar_to_square(photo_bytes)
        except Exception:  # noqa: BLE001
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Не удалось обработать изображение — файл повреждён или формат не поддерживается",
                status_code=303,
            )

        try:
            await update_avatar_standalone(account, photo_bytes)
        except AccountLimitedError as e:
            return RedirectResponse(
                f"/accounts/{account_id}?flash=Аккаунт временно ограничен Telegram ({e.retry_after_seconds} сек)",
                status_code=303,
            )
        except AccountBannedError as e:
            return flash_redirect(f"/accounts/{account_id}", f"Аккаунт забанен: {e}")
        except Exception as e:  # noqa: BLE001
            return flash_redirect(f"/accounts/{account_id}", f"Не удалось подключиться: {e}")

        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        _avatar_path(account_id).write_bytes(photo_bytes)
        account.tg_synced_at = dt.datetime.now(dt.timezone.utc)
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
            return flash_redirect(f"/accounts/{account_id}", f"Аккаунт забанен: {e}")
        except Exception as e:  # noqa: BLE001
            return flash_redirect(f"/accounts/{account_id}", f"Не удалось опубликовать сторис: {e}")

    return RedirectResponse(f"/accounts/{account_id}?flash=Сторис опубликована", status_code=303)
