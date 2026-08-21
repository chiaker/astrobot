from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from astrobot.astro_events import pick_event, scan_events
from astrobot.autopost import (
    DEFAULT_QUESTION,
    _split_post,
    create_campaign,
    generate_post,
    is_due,
    parse_weekdays,
)
from astrobot.bot.keyboards import build_broadcast_kb
from astrobot.db.models import AutopostConfig, AutopostMedia, Broadcast, BroadcastVariant
from astrobot.limits import BROADCAST_SEGMENTS
from astrobot.llm.prompts import AUTOPOST_MARKER
from astrobot.lunar import compute_phases

# ─── scan_events ───────────────────────────────────────────────────────────────

def test_scan_matches_lunar_module_on_lunations():
    start, end = date(2026, 8, 1), date(2026, 10, 1)
    expected = {(p.event_date, p.kind) for p in compute_phases(start, end)}
    got = {
        (e.when, e.key.split(":")[1])
        for e in scan_events(start, end)
        if e.key.startswith("moon_phase:")
    }
    assert got == expected and expected  # the window really does contain lunations


def test_scan_finds_mercury_stations():
    # Mercury retrogrades ~3x a year, so a 200-day window always holds a full
    # retrograde → direct pair.
    keys = [e.key for e in scan_events(date(2026, 1, 10), date(2026, 7, 29))]
    assert any(k.startswith("retro:Mercury:") for k in keys)
    assert any(k.startswith("direct:Mercury:") for k in keys)


def test_scan_finds_sun_ingress_once_a_month():
    # The Sun changes sign every ~30 days; a 40-day window must contain one.
    events = [e for e in scan_events(date(2026, 3, 1), date(2026, 4, 10))
              if e.key.startswith("ingress:Sun:")]
    assert len(events) == 1


def test_scan_never_empty_over_a_pick_window():
    # The Moon changes sign every ~2.5 days, so any 5-day window has a subject —
    # that's what keeps pick_event from ever coming back empty-handed.
    for start in (date(2026, 8, 12), date(2026, 11, 3), date(2027, 2, 20)):
        assert scan_events(start, start + timedelta(days=4))


# ─── transiting aspects ────────────────────────────────────────────────────────

_ASPECT_DEGREES = {
    "conjunction": 0.0, "sextile": 60.0, "square": 90.0,
    "trine": 120.0, "opposition": 180.0,
}


def _separation_and_speed(when, a, b):
    """Actual angular distance between two planets at noon, and how fast that
    distance changes — computed straight from swisseph, independent of the
    scanner's own bookkeeping."""
    import swisseph as swe

    from astrobot.astro_events import _FLAGS, _PLANETS

    jd = swe.julday(when.year, when.month, when.day, 12.0)
    lon_a, spd_a = (lambda x: (x[0], x[3]))(swe.calc_ut(jd, _PLANETS[a][0], _FLAGS)[0])
    lon_b, spd_b = (lambda x: (x[0], x[3]))(swe.calc_ut(jd, _PLANETS[b][0], _FLAGS)[0])
    sep = abs((lon_a - lon_b + 180.0) % 360.0 - 180.0)
    return sep, abs(spd_a - spd_b)


def test_every_reported_aspect_is_really_exact():
    # The scan reports the day the aspect perfects; at noon of that day the two
    # planets must be within one day's relative motion of the exact angle.
    checked = 0
    for e in scan_events(date(2026, 8, 1), date(2026, 12, 1)):
        if not e.key.startswith("aspect:"):
            continue
        _, a, aspect, b, _ = e.key.split(":")
        sep, rel_speed = _separation_and_speed(e.when, a, b)
        assert abs(sep - _ASPECT_DEGREES[aspect]) <= rel_speed + 0.5, e.key
        checked += 1
    assert checked > 20  # four months of sky really is that busy


def test_aspects_never_involve_the_moon():
    # The Moon aspects something every few hours — it would drown out everything.
    for e in scan_events(date(2026, 8, 1), date(2026, 9, 1)):
        assert "Moon" not in e.key or not e.key.startswith("aspect:")


def test_aspect_feed_covers_all_five_major_aspects():
    kinds = {
        e.key.split(":")[2]
        for e in scan_events(date(2026, 1, 1), date(2026, 12, 31))
        if e.key.startswith("aspect:")
    }
    assert kinds == set(_ASPECT_DEGREES)


def test_slow_planet_aspects_outweigh_fast_ones():
    events = {
        (e.key.split(":")[1], e.key.split(":")[2], e.key.split(":")[3]): e.weight
        for e in scan_events(date(2026, 1, 1), date(2026, 12, 31))
        if e.key.startswith("aspect:")
    }
    heavy = max(w for (a, _, b), w in events.items() if {a, b} <= {"Jupiter", "Saturn",
                                                                  "Uranus", "Neptune", "Pluto"})
    light = max(w for (a, _, b), w in events.items() if "Mercury" in (a, b))
    assert heavy > light


# ─── pick_event ────────────────────────────────────────────────────────────────

def test_pick_takes_the_heaviest_event_in_window():
    d = date(2026, 8, 14)
    window = scan_events(d - timedelta(days=1), d + timedelta(days=3))
    assert pick_event(d).weight == max(e.weight for e in window)


def test_pick_skips_the_previously_used_event():
    d = date(2026, 8, 14)
    first = pick_event(d)
    assert pick_event(d, first.key).key != first.key


# ─── is_due: the admin schedule ────────────────────────────────────────────────

MSK = ZoneInfo("Europe/Moscow")
FRIDAY = datetime(2026, 8, 14, 11, 0, tzinfo=MSK)  # weekday() == 4


def _cfg(**kw):
    base = dict(
        enabled=True, interval_days=3, hour_msk=11, weekdays="", last_generated_at=None
    )
    return AutopostConfig(id=1, **(base | kw))


def test_parse_weekdays_ignores_junk():
    assert parse_weekdays("0,2,4") == {0, 2, 4}
    assert parse_weekdays(" 6 ,,x,9,-1") == {6}
    assert parse_weekdays(None) == set() == parse_weekdays("")


def test_not_due_when_disabled_or_off_hour():
    assert not is_due(_cfg(enabled=False), FRIDAY)
    assert not is_due(_cfg(), FRIDAY.replace(hour=12))


def test_interval_mode_waits_out_the_interval():
    assert is_due(_cfg(), FRIDAY)  # never posted yet
    assert not is_due(_cfg(last_generated_at=FRIDAY - timedelta(days=2)), FRIDAY)
    assert is_due(_cfg(last_generated_at=FRIDAY - timedelta(days=3)), FRIDAY)


def test_interval_counts_calendar_days_not_exact_hours():
    # Пост в 11:00:30, тик через трое суток в 11:00:10 — по timedelta это 2 дня,
    # и слот бы пропустился, а расписание поехало бы на день вперёд.
    last = FRIDAY - timedelta(days=3) + timedelta(seconds=20)
    assert is_due(_cfg(last_generated_at=last), FRIDAY)


def test_weekday_mode_fires_only_on_picked_days():
    assert is_due(_cfg(weekdays="0,4"), FRIDAY)
    assert not is_due(_cfg(weekdays="0,2"), FRIDAY)


def test_weekday_mode_posts_once_per_day_and_ignores_the_interval():
    # Picked days win over interval_days: Mon+Fri means Mon+Fri, not "every 3rd day".
    yesterday = _cfg(weekdays="0,4", last_generated_at=FRIDAY - timedelta(days=1))
    assert is_due(yesterday, FRIDAY)
    earlier_today = _cfg(weekdays="0,4", last_generated_at=FRIDAY - timedelta(hours=1))
    assert not is_due(earlier_today, FRIDAY)


def test_naive_timestamp_is_read_as_utc():
    # Postgres returns tz-aware values, but a hand-written row may not.
    naive = (FRIDAY - timedelta(days=3)).astimezone(UTC).replace(tzinfo=None)
    assert is_due(_cfg(last_generated_at=naive), FRIDAY)


# ─── _split_post ───────────────────────────────────────────────────────────────

def test_split_post_separates_text_and_question():
    text, question = _split_post(
        f"<b>🌕 Полнолуние</b>\nЧто-то тёплое.\n{AUTOPOST_MARKER}\n«Как оно на мне скажется?»"
    )
    assert text == "<b>🌕 Полнолуние</b>\nЧто-то тёплое."
    assert question == "Как оно на мне скажется?"  # quotes stripped


def test_split_post_falls_back_when_marker_missing():
    # The post is still good — only the button question is missing. Losing the
    # whole campaign over a formatting slip would be worse.
    text, question = _split_post("Просто пост без маркера.")
    assert text == "Просто пост без маркера."
    assert question == DEFAULT_QUESTION


def test_split_post_ignores_extra_lines_after_the_question():
    _, question = _split_post(f"Пост.\n{AUTOPOST_MARKER}\nМой вопрос?\nлишняя строка")
    assert question == "Мой вопрос?"


POST = "<b>Луна в Весах</b>\nДва дня про равновесие и чужие ожидания."


def _stub_llm(monkeypatch, text):
    llm = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(text=text)))
    monkeypatch.setattr("astrobot.autopost.get_llm", lambda: llm)
    return llm


async def test_empty_completion_is_a_failed_run_not_a_mute_post(monkeypatch):
    # With a gif attached, an empty text still passes _variant_has_content and goes
    # out as a caption-less photo. Fail the run instead — the job retries.
    _stub_llm(monkeypatch, "")
    with pytest.raises(ValueError):
        await generate_post(pick_event(date(2026, 8, 14)))


async def test_answer_with_only_the_question_is_rejected(monkeypatch):
    _stub_llm(monkeypatch, f"{AUTOPOST_MARKER}\nЧто это значит для меня?")
    with pytest.raises(ValueError):
        await generate_post(pick_event(date(2026, 8, 14)))


async def test_unbalanced_html_degrades_to_plain_text(monkeypatch):
    # Один незакрытый тег — и Telegram отвергает сообщение для КАЖДОГО получателя,
    # то есть рассылка не доходит ни до кого. Лучше без жирного, чем никак.
    _stub_llm(monkeypatch, "<b>Заголовок\nТело поста про равновесие и ожидания.")
    text, _ = await generate_post(pick_event(date(2026, 8, 14)))
    assert "<" not in text and "Заголовок" in text


async def test_valid_html_is_kept_and_markdown_is_converted(monkeypatch):
    _stub_llm(monkeypatch, "<b>Луна в Весах</b>\n**Жирный** день про равновесие и покой.")
    text, _ = await generate_post(pick_event(date(2026, 8, 14)))
    assert "<b>Луна в Весах</b>" in text
    assert "**" not in text and "<b>Жирный</b>" in text


async def test_question_is_stripped_of_html(monkeypatch):
    # Вопрос подставляется в «❓ <i>{question}</i>» — тег внутри ломает и это
    # сообщение тоже.
    _stub_llm(monkeypatch, f"{POST}\n{AUTOPOST_MARKER}\n<b>Что это значит для меня?</b>")
    _, question = await generate_post(pick_event(date(2026, 8, 14)))
    assert question == "Что это значит для меня?"


async def test_runaway_generation_is_rejected(monkeypatch):
    _stub_llm(monkeypatch, "очень длинный пост. " * 400)
    with pytest.raises(ValueError):
        await generate_post(pick_event(date(2026, 8, 14)))


async def test_media_is_dropped_when_the_caption_would_be_too_long():
    # С подписью длиннее лимита Telegram отвергает каждую отправку с медиа, и пост
    # доходит текстом только после провального запроса на каждого получателя.
    from astrobot.autopost import CAPTION_LIMIT

    media = AutopostMedia(id=1, animation="FILE", animation_name="a.gif", use_count=0)
    session = _FakeSession()
    long_text = "Очень длинный пост. " * ((CAPTION_LIMIT // 20) + 5)
    await create_campaign(
        session, pick_event(date(2026, 8, 14)), long_text, "Вопрос?",
        schedule=True, media=media,
    )
    variants = [o for o in session.added if isinstance(o, BroadcastVariant)]
    assert all(not v.animation for v in variants)
    assert media.use_count == 0  # и гифка не потрачена впустую


async def test_campaign_refuses_empty_text():
    session = _FakeSession()
    with pytest.raises(ValueError):
        await create_campaign(
            session, pick_event(date(2026, 8, 14)), "  ", "Вопрос?", schedule=True
        )
    assert not session.added  # ничего не создано, откатывать нечего


async def test_generate_post_uses_the_llm_response(monkeypatch):
    llm = _stub_llm(monkeypatch, f"{POST}\n{AUTOPOST_MARKER}\nВопрос?")
    event = pick_event(date(2026, 8, 14))
    assert await generate_post(event) == (POST, "Вопрос?")
    assert event.detail in llm.complete.await_args.kwargs["user_message"]


# ─── create_campaign ───────────────────────────────────────────────────────────

class _FakeSession:
    """Enough of AsyncSession for create_campaign: collect + hand out an id."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if isinstance(obj, Broadcast) and obj.id is None:
                obj.id = 42

    async def commit(self) -> None:
        pass


async def _campaign(schedule: bool) -> tuple[Broadcast, list[BroadcastVariant]]:
    session = _FakeSession()
    event = pick_event(date(2026, 8, 14))
    b = await create_campaign(session, event, POST, "Мой вопрос?", schedule=schedule)
    return b, [o for o in session.added if isinstance(o, BroadcastVariant)]


async def test_campaign_covers_every_segment():
    # _run_broadcast silently skips users whose segment has no variant, so all
    # five must exist and be enabled.
    _, variants = await _campaign(schedule=True)
    assert {v.segment for v in variants} == set(BROADCAST_SEGMENTS)
    assert all(v.enabled and v.text == POST for v in variants)


async def test_campaign_buttons_ask_except_for_not_onboarded():
    _, variants = await _campaign(schedule=True)
    by_seg = {v.segment: v for v in variants}
    assert by_seg["not_onboarded"].buttons[0]["type"] == "onboarding"
    for seg in set(BROADCAST_SEGMENTS) - {"not_onboarded"}:
        btn = by_seg[seg].buttons[0]
        assert btn["type"] == "ask" and btn["value"] == "Мой вопрос?"


async def test_campaign_button_renders_as_an_ask_callback():
    _, variants = await _campaign(schedule=True)
    variant = next(v for v in variants if v.segment == "free_has_questions")
    variant.id = 5
    kb = build_broadcast_kb(variant)
    assert "bcast:ask:5:0" in [b.payload for row in kb.rows for b in row]


async def _campaign_with(media):
    session = _FakeSession()
    event = pick_event(date(2026, 8, 14))
    await create_campaign(session, event, POST, "Вопрос?", schedule=True, media=media)
    return [o for o in session.added if isinstance(o, BroadcastVariant)]


async def test_cached_file_id_is_referenced_not_copied():
    # A gif that has already been sent once is attached by id — copying its bytes
    # into all five variants of every campaign would bloat the DB for nothing.
    media = AutopostMedia(id=1, animation="FILE123", animation_data=b"rawbytes",
                          animation_name="stars.gif", use_count=2)
    variants = await _campaign_with(media)
    assert all(v.animation == "FILE123" and v.animation_data is None for v in variants)
    assert media.use_count == 3


async def test_never_sent_upload_carries_its_bytes():
    media = AutopostMedia(id=1, animation="", animation_data=b"rawbytes",
                          animation_name="stars.gif", use_count=0)
    variants = await _campaign_with(media)
    assert all(v.animation_data == b"rawbytes" for v in variants)
    assert all(v.animation_name == "stars.gif" for v in variants)


async def test_empty_pool_still_produces_a_text_post():
    variants = await _campaign_with(None)
    assert all(not v.animation and v.animation_data is None for v in variants)


async def test_scheduled_campaign_is_due_immediately_and_draft_is_not():
    scheduled, _ = await _campaign(schedule=True)
    assert scheduled.status == "scheduled" and scheduled.scheduled_at is not None
    draft, _ = await _campaign(schedule=False)
    assert draft.status == "draft" and draft.scheduled_at is None
