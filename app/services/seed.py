"""Seeds built-in tone/persona presets on startup. Idempotent — safe to call
from both the web process and the worker process."""
from __future__ import annotations

from sqlalchemy import select

from app.db import async_session_factory
from app.models import Persona

BUILTIN_PERSONAS: list[tuple[str, str]] = [
    (
        "Нейтральный",
        "нейтральный, дружелюбный подписчик без выраженных особенностей речи, пишет просто и по делу",
    ),
    (
        "Саркастичный",
        "саркастичный, слегка ироничный подписчик, любит подколоть и пошутить, но не переходит на грубость",
    ),
    (
        "Экспертный",
        "сдержанный подписчик с экспертным тоном, добавляет фактическую деталь или уточнение по теме поста",
    ),
    (
        "Эмоциональный",
        "эмоциональный подписчик, живо реагирует, использует восклицания и выражает искренние чувства по поводу поста",
    ),
    (
        "Лаконичный",
        "немногословный подписчик, отвечает очень коротко, одной фразой, без лишних слов",
    ),
]


async def seed_builtin_personas() -> None:
    async with async_session_factory() as session:
        existing_names = set(
            (await session.execute(select(Persona.name).where(Persona.is_builtin == True))).scalars().all()  # noqa: E712
        )
        for name, prompt in BUILTIN_PERSONAS:
            if name not in existing_names:
                session.add(Persona(name=name, prompt_text=prompt, is_builtin=True))
        await session.commit()
