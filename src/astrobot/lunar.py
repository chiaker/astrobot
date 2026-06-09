from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

import swisseph as swe

LunarKind = Literal["new", "full"]


@dataclass
class LunarPhase:
    event_date: date
    kind: LunarKind


def _diff_at(year: int, month: int, day: int, hour: float = 12.0) -> float:
    """Moon ecliptic longitude minus Sun's, normalised to [0, 360)."""
    jd = swe.julday(year, month, day, hour)
    sun_lon = swe.calc_ut(jd, swe.SUN)[0][0]
    moon_lon = swe.calc_ut(jd, swe.MOON)[0][0]
    return (moon_lon - sun_lon) % 360.0


def compute_phases(start: date, end: date) -> list[LunarPhase]:
    """Find new and full moon dates in [start, end] (inclusive)."""
    events: list[LunarPhase] = []
    prev = _diff_at(start.year, start.month, start.day)
    d = start + timedelta(days=1)
    while d <= end:
        cur = _diff_at(d.year, d.month, d.day)
        # Approximations: diff wraps 360→0 at new moon; crosses 180 at full moon.
        if prev > 340.0 and cur < 20.0:
            events.append(LunarPhase(event_date=d, kind="new"))
        elif prev < 180.0 <= cur:
            events.append(LunarPhase(event_date=d, kind="full"))
        prev = cur
        d += timedelta(days=1)
    return events


def phase_text(kind: LunarKind) -> str:
    if kind == "new":
        return (
            "🌑 <b>Сегодня новолуние.</b>\n\n"
            "Лунный цикл начинается заново. Хорошее время задать себе намерение "
            "на ближайший месяц — записать на бумаге одну вещь, которую хочется "
            "впустить в свою жизнь. Звёзды любят, когда им помогают делом ✨"
        )
    return (
        "🌕 <b>Сегодня полнолуние.</b>\n\n"
        "Время кульминации: то, что копилось последние две недели, выходит на свет. "
        "Хорошо отпустить то, что больше не служит — старую обиду, привычку, ожидание. "
        "Маленькие итоги полезнее больших решений ✨"
    )


def horizon_dates() -> tuple[date, date]:
    """Today through 30 days ahead, used to refresh the cache table."""
    today = datetime.utcnow().date()
    return today, today + timedelta(days=30)
