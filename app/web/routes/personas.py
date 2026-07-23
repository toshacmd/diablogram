from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.db import async_session_factory
from app.models import Persona
from app.web.templating import templates

router = APIRouter()


@router.get("/personas")
async def list_personas(request: Request):
    async with async_session_factory() as session:
        personas = (await session.execute(select(Persona).order_by(Persona.is_builtin.desc(), Persona.name))).scalars().all()
    return templates.TemplateResponse(
        request, "personas.html", {"active": "personas", "personas": personas}
    )


@router.post("/personas/add")
async def add_persona(name: str = Form(...), prompt_text: str = Form(...)):
    async with async_session_factory() as session:
        session.add(Persona(name=name.strip(), prompt_text=prompt_text.strip(), is_builtin=False))
        await session.commit()
    return RedirectResponse("/personas?flash=Персона добавлена", status_code=303)


@router.post("/personas/{persona_id}/update")
async def update_persona(persona_id: int, name: str = Form(...), prompt_text: str = Form(...)):
    async with async_session_factory() as session:
        persona = await session.get(Persona, persona_id)
        if persona:
            persona.name = name.strip()
            persona.prompt_text = prompt_text.strip()
            await session.commit()
    return RedirectResponse("/personas?flash=Сохранено", status_code=303)


@router.post("/personas/{persona_id}/delete")
async def delete_persona(persona_id: int):
    async with async_session_factory() as session:
        persona = await session.get(Persona, persona_id)
        if persona and not persona.is_builtin:
            await session.delete(persona)
            await session.commit()
    return RedirectResponse("/personas?flash=Удалено", status_code=303)
