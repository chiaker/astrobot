from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol

import structlog
from anthropic import AsyncAnthropic

from astrobot.config import get_settings
from astrobot.metrics import LLM_CALLS_TOTAL, LLM_DURATION, LLM_TOKENS_TOTAL

log = structlog.get_logger(__name__)

Role = Literal["user", "assistant"]


@dataclass
class HistoryMessage:
    role: Role
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    async def complete(
        self,
        system: str,
        cached_context: str,
        user_message: str,
        history: list[HistoryMessage] | None = None,
        max_tokens: int = 1500,
        kind: str = "generic",
    ) -> LLMResponse: ...


class AnthropicClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncAnthropic(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            timeout=90.0,
            max_retries=2,
        )
        self._model = settings.llm_model

    async def complete(
        self,
        system: str,
        cached_context: str,
        user_message: str,
        history: list[HistoryMessage] | None = None,
        max_tokens: int = 1500,
        kind: str = "generic",
    ) -> LLMResponse:
        system_blocks = [
            {"type": "text", "text": system},
            {
                "type": "text",
                "text": cached_context,
                "cache_control": {"type": "ephemeral"},
            },
        ]

        messages: list[dict] = []
        for m in history or []:
            messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": user_message})

        start = time.monotonic()
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
            )
        except Exception as e:
            LLM_CALLS_TOTAL.labels(kind=kind, model=self._model, status="error").inc()
            log.warning("llm_call_failed", kind=kind, error_type=type(e).__name__)
            raise
        finally:
            LLM_DURATION.labels(kind=kind, model=self._model).observe(time.monotonic() - start)

        text = "".join(block.text for block in resp.content if block.type == "text")
        usage = resp.usage
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        LLM_CALLS_TOTAL.labels(kind=kind, model=self._model, status="ok").inc()
        LLM_TOKENS_TOTAL.labels(kind=kind, model=self._model, direction="input").inc(usage.input_tokens)
        LLM_TOKENS_TOTAL.labels(kind=kind, model=self._model, direction="cache_creation").inc(cache_creation)
        LLM_TOKENS_TOTAL.labels(kind=kind, model=self._model, direction="cache_read").inc(cache_read)
        LLM_TOKENS_TOTAL.labels(kind=kind, model=self._model, direction="output").inc(usage.output_tokens)

        log.info(
            "llm_complete",
            kind=kind,
            model=self._model,
            input=usage.input_tokens,
            cache_creation=cache_creation,
            cache_read=cache_read,
            output=usage.output_tokens,
        )
        return LLMResponse(
            text=text,
            model=self._model,
            input_tokens=usage.input_tokens,
            cached_input_tokens=cache_creation + cache_read,
            output_tokens=usage.output_tokens,
        )


_default: AnthropicClient | None = None


def get_llm() -> LLMClient:
    global _default
    if _default is None:
        _default = AnthropicClient()
    return _default
