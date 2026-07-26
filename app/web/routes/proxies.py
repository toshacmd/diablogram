from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update as sa_update

from app.crypto import encrypt
from app.db import async_session_factory
from app.models import Account, Proxy
from app.web.flash import flash_redirect
from app.web.templating import templates

router = APIRouter()

_VALID_TYPES = {"socks5", "socks4", "http"}


@router.get("/proxies")
async def list_proxies(request: Request):
    async with async_session_factory() as session:
        proxies = (await session.execute(select(Proxy).order_by(Proxy.id))).scalars().all()
        usage = dict(
            (
                await session.execute(
                    select(Account.proxy_id, func.count(Account.id))
                    .where(Account.proxy_id.is_not(None))
                    .group_by(Account.proxy_id)
                )
            ).all()
        )
    return templates.TemplateResponse(
        request, "proxies.html", {"active": "proxies", "proxies": proxies, "usage": usage}
    )


@router.post("/proxies/add")
async def add_proxy(
    label: str = Form(""),
    proxy_type: str = Form(...),
    proxy_host: str = Form(...),
    proxy_port: str = Form(...),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
):
    if proxy_type not in _VALID_TYPES:
        return RedirectResponse("/proxies?flash=Неизвестный тип прокси", status_code=303)
    try:
        port = int(proxy_port.strip())
    except ValueError:
        return RedirectResponse("/proxies?flash=Порт должен быть числом", status_code=303)
    async with async_session_factory() as session:
        session.add(
            Proxy(
                label=label.strip() or None,
                proxy_type=proxy_type,
                proxy_host=proxy_host.strip(),
                proxy_port=port,
                proxy_username=proxy_username.strip() or None,
                proxy_password_enc=encrypt(proxy_password) if proxy_password else None,
            )
        )
        await session.commit()
    return RedirectResponse("/proxies?flash=Прокси добавлен", status_code=303)


@router.post("/proxies/{proxy_id}/update")
async def update_proxy(
    proxy_id: int,
    label: str = Form(""),
    proxy_type: str = Form(...),
    proxy_host: str = Form(...),
    proxy_port: str = Form(...),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
):
    if proxy_type not in _VALID_TYPES:
        return RedirectResponse("/proxies?flash=Неизвестный тип прокси", status_code=303)
    try:
        port = int(proxy_port.strip())
    except ValueError:
        return RedirectResponse("/proxies?flash=Порт должен быть числом", status_code=303)
    async with async_session_factory() as session:
        proxy = await session.get(Proxy, proxy_id)
        if proxy is None:
            return RedirectResponse("/proxies?flash=Прокси не найден", status_code=303)
        proxy.label = label.strip() or None
        proxy.proxy_type = proxy_type
        proxy.proxy_host = proxy_host.strip()
        proxy.proxy_port = port
        proxy.proxy_username = proxy_username.strip() or None
        if proxy_password:
            proxy.proxy_password_enc = encrypt(proxy_password)
        await session.commit()
    return RedirectResponse(
        "/proxies?flash=Сохранено. Аккаунты, использующие этот прокси, продолжают работать со старыми "
        "значениями — чтобы применить новые, выберите прокси заново в карточке аккаунта",
        status_code=303,
    )


@router.post("/proxies/{proxy_id}/delete")
async def delete_proxy(proxy_id: int):
    async with async_session_factory() as session:
        proxy = await session.get(Proxy, proxy_id)
        if proxy is None:
            return RedirectResponse("/proxies?flash=Прокси не найден", status_code=303)
        name = proxy.display_name()
        # Accounts keep their copied inline proxy values and keep working —
        # only the provenance link is cleared. Done explicitly (not via
        # ON DELETE SET NULL alone) because SQLite in dev doesn't enforce FKs.
        await session.execute(
            sa_update(Account).where(Account.proxy_id == proxy_id).values(proxy_id=None)
        )
        await session.delete(proxy)
        await session.commit()
    return flash_redirect("/proxies", f"Прокси «{name}» удалён. Аккаунты, где он был выбран, продолжают работать с прежними настройками")
