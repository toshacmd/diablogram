import csv
import io

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.crypto import encrypt
from app.db import async_session_factory
from app.models import AccountStatus, ParsedChannel, ParseRun, ParseRunStatus, ScrapeAccount
from app.web.templating import templates

router = APIRouter()


@router.get("/parsing")
async def parsing_home(request: Request):
    async with async_session_factory() as session:
        scrape_accounts = (
            (await session.execute(select(ScrapeAccount).order_by(ScrapeAccount.id))).scalars().all()
        )
        runs = (await session.execute(select(ParseRun).order_by(ParseRun.created_at.desc()))).scalars().all()

    return templates.TemplateResponse(
        request,
        "parsing.html",
        {
            "active": "parsing",
            "scrape_accounts": scrape_accounts,
            "runs": runs,
        },
    )


@router.post("/parsing/scrape-accounts/add")
async def add_scrape_account(
    request: Request,
    label: str = Form(""),
    session_string: str = Form(...),
    proxy_type: str = Form(""),
    proxy_host: str = Form(""),
    proxy_port: str = Form(""),
    proxy_username: str = Form(""),
    proxy_password: str = Form(""),
):
    async with async_session_factory() as session:
        session.add(
            ScrapeAccount(
                label=label.strip() or None,
                session_string_enc=encrypt(session_string.strip()),
                proxy_type=proxy_type or None,
                proxy_host=proxy_host or None,
                proxy_port=int(proxy_port) if proxy_port else None,
                proxy_username=proxy_username or None,
                proxy_password_enc=encrypt(proxy_password) if proxy_password else None,
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()
    return RedirectResponse("/parsing?flash=Scrape-аккаунт добавлен", status_code=303)


@router.post("/parsing/scrape-accounts/{account_id}/delete")
async def delete_scrape_account(account_id: int):
    async with async_session_factory() as session:
        account = await session.get(ScrapeAccount, account_id)
        if account is None:
            return RedirectResponse("/parsing?flash=Аккаунт не найден", status_code=303)
        await session.delete(account)
        await session.commit()
    return RedirectResponse("/parsing?flash=Scrape-аккаунт удалён", status_code=303)


@router.post("/parsing/runs/start")
async def start_parse_run(
    keywords: str = Form(...),
    min_subscribers: int = Form(1000),
    max_inactive_days: int = Form(14),
    depth: int = Form(1),
):
    if not keywords.strip():
        return RedirectResponse("/parsing?flash=Нужно указать хотя бы одно ключевое слово", status_code=303)
    async with async_session_factory() as session:
        run = ParseRun(
            keywords=keywords.strip(),
            min_subscribers=max(0, min_subscribers),
            max_inactive_days=max(1, max_inactive_days),
            depth=max(0, min(depth, 2)),
            status=ParseRunStatus.QUEUED,
        )
        session.add(run)
        await session.commit()
    return RedirectResponse("/parsing?flash=Прогон поставлен в очередь", status_code=303)


@router.get("/parsing/runs/{run_id}")
async def parse_run_detail(request: Request, run_id: int):
    async with async_session_factory() as session:
        run = await session.get(ParseRun, run_id)
        if run is None:
            return RedirectResponse("/parsing?flash=Прогон не найден", status_code=303)
        channels = (
            (
                await session.execute(
                    select(ParsedChannel)
                    .where(ParsedChannel.run_id == run_id)
                    .order_by(ParsedChannel.subscriber_count.desc())
                )
            )
            .scalars()
            .all()
        )

    return templates.TemplateResponse(
        request,
        "parsing_run.html",
        {
            "active": "parsing",
            "run": run,
            "channels": channels,
        },
    )


@router.get("/parsing/runs/{run_id}/export.csv")
async def export_parse_run_csv(run_id: int):
    async with async_session_factory() as session:
        run = await session.get(ParseRun, run_id)
        if run is None:
            return RedirectResponse("/parsing?flash=Прогон не найден", status_code=303)
        channels = (
            (
                await session.execute(
                    select(ParsedChannel)
                    .where(ParsedChannel.run_id == run_id)
                    .order_by(ParsedChannel.subscriber_count.desc())
                )
            )
            .scalars()
            .all()
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["tg_channel_id", "title", "username", "subscribers", "link"])
    for c in channels:
        writer.writerow([c.tg_channel_id, c.title, c.username, c.subscriber_count, f"https://t.me/{c.username}"])

    content = "﻿" + buf.getvalue()  # BOM so Excel renders Cyrillic correctly
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="parse_run_{run_id}.csv"'},
    )
