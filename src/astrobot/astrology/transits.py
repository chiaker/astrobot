from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from kerykeion import AspectsFactory, AstrologicalSubjectFactory

from astrobot.astrology.chart import build_subject
from astrobot.astrology.ru import aspect_ru, planet_ru, sign_ru
from astrobot.astrology.types import BirthData

Period = Literal["today", "week", "month"]

NATAL_TARGETS_BASE: frozenset[str] = frozenset(
    {"Sun", "Moon", "Mercury", "Venus", "Mars"}
)
NATAL_TARGETS_WITH_ANGLES: frozenset[str] = NATAL_TARGETS_BASE | {
    "Ascendant",
    "Medium_Coeli",
}
SLOW_TRANSIT_PLANETS: frozenset[str] = frozenset(
    {"Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"}
)
FAST_TRANSIT_PLANETS: frozenset[str] = frozenset(
    {"Sun", "Moon", "Mercury", "Venus"}
)

MAJOR_ASPECTS: frozenset[str] = frozenset(
    {"conjunction", "opposition", "trine", "square", "sextile"}
)


@dataclass
class TransitAspect:
    natal_planet: str
    transit_planet: str
    aspect: str
    orb: float
    transit_sign: str
    movement: str | None


@dataclass
class TransitReport:
    period: Period
    start: date
    end: date
    transits: list[TransitAspect]


def _orb_limit(period: Period, transit_planet: str) -> float:
    if transit_planet in SLOW_TRANSIT_PLANETS:
        return {"today": 1.5, "week": 2.5, "month": 3.5}[period]
    return {"today": 2.0, "week": 1.5, "month": 0.5}[period]


def _period_range(today: date, period: Period) -> tuple[date, date]:
    if period == "today":
        return today, today
    if period == "week":
        return today, today + timedelta(days=6)
    return today, today + timedelta(days=29)


def _midpoint(start: date, end: date) -> date:
    return start + (end - start) / 2


def build_transit_report(
    birth: BirthData,
    today: date,
    period: Period,
) -> TransitReport:
    start, end = _period_range(today, period)
    midpoint = _midpoint(start, end)

    natal = build_subject(birth)
    natal_name = natal.name

    transit_subject = AstrologicalSubjectFactory.from_birth_data(
        name="Transit",
        year=midpoint.year,
        month=midpoint.month,
        day=midpoint.day,
        hour=12,
        minute=0,
        lat=birth.lat,
        lng=birth.lon,
        tz_str=birth.tz,
        city=birth.city_name,
        online=False,
        houses_system_identifier="P",
    )

    dual = AspectsFactory.dual_chart_aspects(natal, transit_subject)

    allowed_targets = (
        NATAL_TARGETS_WITH_ANGLES if not birth.time_unknown else NATAL_TARGETS_BASE
    )

    transit_planets = SLOW_TRANSIT_PLANETS | (
        FAST_TRANSIT_PLANETS if period == "today" else frozenset()
    )

    transit_planet_signs: dict[str, str] = {}
    for attr in (
        "sun",
        "moon",
        "mercury",
        "venus",
        "mars",
        "jupiter",
        "saturn",
        "uranus",
        "neptune",
        "pluto",
    ):
        p = getattr(transit_subject, attr, None)
        if p is not None:
            transit_planet_signs[p.name] = p.sign

    selected: list[TransitAspect] = []
    for asp in dual.aspects:
        if asp.aspect not in MAJOR_ASPECTS:
            continue
        if asp.p1_owner != natal_name or asp.p2_owner != transit_subject.name:
            continue
        if asp.p1_name not in allowed_targets:
            continue
        if asp.p2_name not in transit_planets:
            continue
        if asp.orbit > _orb_limit(period, asp.p2_name):
            continue
        selected.append(
            TransitAspect(
                natal_planet=asp.p1_name,
                transit_planet=asp.p2_name,
                aspect=asp.aspect,
                orb=float(asp.orbit),
                transit_sign=transit_planet_signs.get(asp.p2_name, ""),
                movement=getattr(asp, "aspect_movement", None),
            )
        )

    selected.sort(key=lambda t: t.orb)
    return TransitReport(period=period, start=start, end=end, transits=selected)


def transit_report_to_markdown(report: TransitReport) -> str:
    period_ru = {"today": "сегодня", "week": "ближайшая неделя", "month": "ближайший месяц"}[report.period]
    lines: list[str] = []
    lines.append(f"# Транзиты — {period_ru}")
    if report.start == report.end:
        lines.append(f"- Дата: {report.start.strftime('%d.%m.%Y')}")
    else:
        lines.append(
            f"- Период: {report.start.strftime('%d.%m.%Y')} — {report.end.strftime('%d.%m.%Y')}"
        )
    lines.append("")

    if not report.transits:
        lines.append("В этот период значимых транзитов к личным планетам нет.")
        return "\n".join(lines)

    lines.append("## Значимые транзиты")
    for t in report.transits:
        nat = planet_ru(t.natal_planet)
        tra = planet_ru(t.transit_planet)
        sign = sign_ru(t.transit_sign) if t.transit_sign else ""
        asp = aspect_ru(t.aspect)
        sign_str = f" (в знаке {sign})" if sign else ""
        move = f", {t.movement.lower()}" if t.movement else ""
        lines.append(
            f"- транзитный {tra}{sign_str} {asp} к натальному {nat}, орб {t.orb:.1f}°{move}"
        )

    return "\n".join(lines)


def midnight_today_in(tz: str) -> date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz)).date()
