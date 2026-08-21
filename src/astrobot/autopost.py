"""LLM-authored astro autopost: turns a sky event into a ready broadcast campaign.

Nothing here sends anything. It creates a normal Broadcast (+ one variant per
segment), and the existing broadcast_dispatch_job delivers it — so retries,
cursors, rate-limit backoff and the admin's cancel button all come for free.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astro_events import AstroEvent
from astrobot.bot.formatting import md_to_telegram_html, strip_html
from astrobot.db.models import AutopostMedia, Broadcast, BroadcastVariant
from astrobot.limits import BROADCAST_SEGMENTS
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import AUTOPOST_MARKER, build_system_autopost

log = structlog.get_logger()

# Used when the model ignores the marker — the post is still fine, only the
# button question is missing, and an empty `ask` value would drop the button.
DEFAULT_QUESTION = "Как этот период отражается на моей карте?"

# Всё, что короче — не пост, а обрывок: модель вернула пусто, один вопрос или
# оборванную строку. Настоящий пост это 70–120 слов, так что порог с большим
# запасом и живой текст им не отсечь.
MIN_POST_CHARS = 40
# Верхняя граница: 120 слов — это ~900 символов, а лимит сообщения в Telegram
# 4096. Всё, что between, — сорвавшаяся генерация; лучше перегенерить, чем резать
# текст посередине (обрезка ломает теги, а сломанные теги ломают всю рассылку).
MAX_POST_CHARS = 2000
# Лимит подписи к медиа в Telegram. Длинный пост с картинкой не уйдёт вообще, а
# без картинки — уйдёт: в таком случае жертвуем картинкой, а не постом.
CAPTION_LIMIT = 1024
# Бюджет на генерацию. Сам пост — это ~400 токенов, но у рассуждающих моделей в
# этот же лимит попадают reasoning-токены, и на 700 текст обрывался на полуслове.
# Платим только за реально сгенерированное, так что запас ничего не стоит.
POST_TOKEN_BUDGET = 2500

ASK_LABEL = "🌙 Спросить Астру"
ONBOARDING_LABEL = "✨ Познакомиться с Астрой"

_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


WEEKDAY_LABELS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def parse_weekdays(raw: str | None) -> set[int]:
    """"0,2,4" → {0, 2, 4} (Monday = 0). Junk is dropped, empty = interval mode."""
    out = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            out.add(int(part))
    return out


def is_due(cfg, now_msk: datetime) -> bool:
    """Should a post be generated at this tick? Two modes, both at hour_msk:
    picked weekdays (at most one post per chosen day), or — when no weekday is
    picked — the fully automatic «every interval_days days»."""
    if not cfg.enabled or now_msk.hour != cfg.hour_msk:
        return False
    last = cfg.last_generated_at
    if last is not None:
        last = last.replace(tzinfo=UTC) if last.tzinfo is None else last
        last = last.astimezone(now_msk.tzinfo)

    weekdays = parse_weekdays(cfg.weekdays)
    if weekdays:
        if now_msk.weekday() not in weekdays:
            return False
        return last is None or last.date() < now_msk.date()
    # По календарным дням, а не по timedelta: пост в 11:00:30 и следующий тик в
    # 11:00:10 через трое суток дают .days == 2, и слот бы пропустился.
    return last is None or (now_msk.date() - last.date()).days >= cfg.interval_days


_TAG_RE = re.compile(r"<(/?)(\w[\w-]*)[^>]*>")


def _tags_balanced(s: str) -> bool:
    stack: list[str] = []
    for slash, tag in _TAG_RE.findall(s):
        tag = tag.lower()
        if slash:
            if not stack or stack.pop() != tag:
                return False
        else:
            stack.append(tag)
    return not stack


def _safe_html(text: str) -> str:
    """Telegram отвергает всё сообщение целиком из-за одного незакрытого тега — а
    у рассылки это значит «не дошло ни до кого». Сначала прогоняем через общий
    санитайзер (он же чинит markdown и чужие теги), и если разметка всё-таки не
    сходится — отправляем простым текстом: без жирного, но с содержимым."""
    out = md_to_telegram_html(text)
    if _tags_balanced(out):
        return out
    log.warning("autopost_unbalanced_html", preview=out[:120])
    return strip_html(out)


def _split_post(raw: str) -> tuple[str, str]:
    """Split the completion into (post text, first-person button question)."""
    text, _, tail = raw.strip().partition(AUTOPOST_MARKER)
    question = tail.strip().splitlines()[0].strip() if tail.strip() else ""
    # A stray marker variant ("--- ВОПРОС ---") leaves the question in the text;
    # better to ship the post with a generic question than to lose it entirely.
    # The question is echoed inside <i>…</i> by the ask handler, so it goes plain.
    question = strip_html(question).strip("«»\"' ")
    return _safe_html(text), (question or DEFAULT_QUESTION)


def _event_prompt(event: AstroEvent) -> str:
    when = f"{event.when.day} {_MONTHS_RU[event.when.month - 1]}"
    return f"Событие: {event.detail}\nДата события: {when}."


async def generate_post(event: AstroEvent) -> tuple[str, str]:
    """Write the post. Raises when the model gave back nothing usable — the caller
    treats that as a failed run and retries, instead of queueing a mute campaign.
    (LLMClient.complete returns an empty string after its own retry rather than
    raising, and an empty caption on a gif still counts as sendable content.)"""
    resp = await get_llm().complete(
        system=build_system_autopost(),
        cached_context="",
        user_message=_event_prompt(event),
        max_tokens=POST_TOKEN_BUDGET,
        kind="autopost",
    )
    # Обрыв по лимиту токенов даёт текст без концовки и без маркера с вопросом.
    # Такое в рассылку не годится: лучше упасть и попробовать на следующем тике.
    if resp.finish_reason == "length":
        raise ValueError("ответ модели оборван лимитом токенов")
    text, question = _split_post(resp.text)
    if len(text) < MIN_POST_CHARS:
        raise ValueError(
            f"пустой или обрезанный пост ({len(text)} символов, "
            f"ответ модели — {len(resp.text or '')})"
        )
    if len(text) > MAX_POST_CHARS:
        raise ValueError(f"пост длиннее разумного ({len(text)} символов)")
    return text, question


def _buttons(segment: str, question: str) -> list[dict]:
    # Без натальной карты вопрос задать нельзя (need_profile_ctx уведёт в сбор
    # данных рождения) — этому сегменту сразу предлагаем онбординг.
    if segment == "not_onboarded":
        return [{"type": "onboarding", "label": ONBOARDING_LABEL, "value": ""}]
    return [{"type": "ask", "label": ASK_LABEL, "value": question}]


async def pick_media(session: AsyncSession) -> AutopostMedia | None:
    """Next animation from the pool: the least used one, so the pool rotates
    evenly and the same gif never lands on two posts in a row. None = empty pool
    → text-only post, exactly as before."""
    return await session.scalar(
        select(AutopostMedia).order_by(AutopostMedia.use_count, AutopostMedia.id).limit(1)
    )


def _media_fields(media: AutopostMedia | None) -> dict:
    """Animation columns for a variant. A cached file_id is referenced by string
    (no blob copied); only a never-sent upload carries its bytes into the campaign,
    and the send caches the id afterwards."""
    if media is None:
        return {}
    media.use_count += 1
    if media.animation:
        return {"animation": media.animation, "animation_name": media.animation_name}
    return {
        "animation_data": media.animation_data,
        "animation_name": media.animation_name,
    }


async def create_campaign(
    session: AsyncSession,
    event: AstroEvent,
    text: str,
    question: str,
    *,
    schedule: bool,
    media: AutopostMedia | None = None,
) -> Broadcast:
    """Create the campaign. schedule=True → goes out on the next dispatch tick;
    schedule=False → draft the admin can edit and send by hand."""
    # Последний рубеж: с картинкой в варианте пустой текст проходит проверку
    # _variant_has_content и уезжает подписью-пустышкой. Лучше не создать кампанию.
    if len(text.strip()) < MIN_POST_CHARS:
        raise ValueError(f"нечего рассылать: текст короче {MIN_POST_CHARS} символов")

    # Подпись длиннее лимита — Telegram отвергает КАЖДУЮ отправку с медиа, и пост
    # доходит до всех текстом, но только после провального запроса на каждого.
    # Дешевле сразу отцепить картинку.
    if media is not None and len(strip_html(text)) > CAPTION_LIMIT:
        log.info("autopost_media_dropped_long_text", chars=len(strip_html(text)))
        media = None

    now = datetime.now(UTC)
    broadcast = Broadcast(
        name=f"🤖 {event.title} · {event.when.isoformat()}",
        status="scheduled" if schedule else "draft",
        scheduled_at=now if schedule else None,
    )
    session.add(broadcast)
    await session.flush()  # need broadcast.id for the variants

    animation = _media_fields(media)
    for segment in BROADCAST_SEGMENTS:
        session.add(
            BroadcastVariant(
                broadcast_id=broadcast.id,
                segment=segment,
                enabled=True,
                text=text,
                buttons=_buttons(segment, question),
                **animation,
            )
        )
    await session.commit()
    log.info(
        "autopost_created",
        broadcast_id=broadcast.id,
        event_key=event.key,  # `event` is structlog's own message kwarg
        scheduled=schedule,
        media_id=media.id if media else None,
    )
    return broadcast
