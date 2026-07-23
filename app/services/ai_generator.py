"""AI comment generation, provider-agnostic (Anthropic Claude or OpenAI).

Generates only the reactive comment body. The account's static signature is
appended separately by the caller — never by the model.
"""
from __future__ import annotations

import abc

from app.config import get_settings

_SYSTEM_PROMPT_TEMPLATE = """\
Ты — обычный подписчик Telegram-канала, который оставляет комментарий под постом.
Твоя личность и тон: {persona}

Правила:
- Пиши только на русском языке.
- Комментарий короткий и естественный: 1–3 предложения, как пишет живой человек, а не бот.
- Реагируй по существу на содержание поста, без общих фраз, которые подошли бы к любому посту.
- Не упоминай, что ты ИИ, бот или ассистент.
- Не используй хэштеги, если только это не характерно для указанного тона.
- Не добавляй никаких подписей, ссылок или префиксов — только сам текст комментария.
- Не используй кавычки вокруг всего ответа.
"""


class CommentGenerator(abc.ABC):
    @abc.abstractmethod
    async def generate(self, post_text: str, persona_prompt: str) -> str: ...


class AnthropicCommentGenerator(CommentGenerator):
    def __init__(self) -> None:
        from anthropic import AsyncAnthropic

        settings = get_settings()
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    async def generate(self, post_text: str, persona_prompt: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=200,
            system=_SYSTEM_PROMPT_TEMPLATE.format(persona=persona_prompt),
            messages=[{"role": "user", "content": f"Текст поста:\n{post_text}"}],
        )
        return "".join(block.text for block in response.content if block.type == "text").strip()


class OpenAICommentGenerator(CommentGenerator):
    def __init__(self) -> None:
        from openai import AsyncOpenAI

        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
        self._model = settings.openai_model

    async def generate(self, post_text: str, persona_prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=200,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT_TEMPLATE.format(persona=persona_prompt)},
                {"role": "user", "content": f"Текст поста:\n{post_text}"},
            ],
        )
        return (response.choices[0].message.content or "").strip()


def get_comment_generator() -> CommentGenerator:
    provider = get_settings().ai_provider.lower()
    if provider == "anthropic":
        return AnthropicCommentGenerator()
    if provider == "openai":
        return OpenAICommentGenerator()
    raise ValueError(f"Unknown AI_PROVIDER: {provider!r} (expected 'anthropic' or 'openai')")
