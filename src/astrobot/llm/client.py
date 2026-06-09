from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol

import structlog
from openai import AsyncOpenAI

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
    reasoning_tokens: int = 0


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


def _model_for_kind(kind: str) -> str:
    settings = get_settings()
    if kind == "natal" and settings.llm_model_natal:
        return settings.llm_model_natal
    if kind.startswith("horoscope") and settings.llm_model_horoscope:
        return settings.llm_model_horoscope
    if kind == "question" and settings.llm_model_question:
        return settings.llm_model_question
    return settings.llm_model


class DeepSeekClient:
    """OpenAI-compatible client (DeepSeek + any /v1/chat/completions endpoint)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            timeout=90.0,
            max_retries=2,
        )

    async def complete(
        self,
        system: str,
        cached_context: str,
        user_message: str,
        history: list[HistoryMessage] | None = None,
        max_tokens: int = 1500,
        kind: str = "generic",
    ) -> LLMResponse:
        model = _model_for_kind(kind)
        system_text = system + "\n\n" + cached_context

        messages: list[dict] = [{"role": "system", "content": system_text}]
        for m in history or []:
            messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": user_message})

        text = ""
        resp = None
        start = time.monotonic()
        try:
            for attempt in range(2):
                budget = max_tokens * (2 if attempt > 0 else 1)
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=budget,
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    break
                log.warning(
                    "llm_empty_content_retry",
                    kind=kind,
                    model=model,
                    attempt=attempt,
                    finish_reason=resp.choices[0].finish_reason,
                )
        except Exception as e:
            LLM_CALLS_TOTAL.labels(kind=kind, model=model, status="error").inc()
            log.warning("llm_call_failed", kind=kind, error_type=type(e).__name__)
            raise
        finally:
            LLM_DURATION.labels(kind=kind, model=model).observe(time.monotonic() - start)

        assert resp is not None
        usage = resp.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        reasoning = 0
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            reasoning = getattr(details, "reasoning_tokens", 0) or 0

        LLM_CALLS_TOTAL.labels(kind=kind, model=model, status="ok").inc()
        LLM_TOKENS_TOTAL.labels(kind=kind, model=model, direction="input").inc(input_tokens)
        LLM_TOKENS_TOTAL.labels(kind=kind, model=model, direction="cache_read").inc(cache_hit)
        LLM_TOKENS_TOTAL.labels(kind=kind, model=model, direction="output").inc(output_tokens)
        if reasoning:
            LLM_TOKENS_TOTAL.labels(kind=kind, model=model, direction="reasoning").inc(reasoning)

        log.info(
            "llm_complete",
            kind=kind,
            model=model,
            input=input_tokens,
            cache_hit=cache_hit,
            output=output_tokens,
            reasoning=reasoning,
        )
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=input_tokens,
            cached_input_tokens=cache_hit,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning,
        )


_default: LLMClient | None = None


def get_llm() -> LLMClient:
    global _default
    if _default is None:
        _default = DeepSeekClient()
    return _default
