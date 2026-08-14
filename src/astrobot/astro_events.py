"""Global (chart-independent) astrological events, straight from the ephemeris.

Used by the autopost generator to pick a subject for the next broadcast. Unlike
`astrology.transits`, nothing here needs a birth chart — these are events in the
sky that are the same for everyone: lunations, retrograde stations, ingresses and
the transiting aspects planets make to each other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import swisseph as swe

from astrobot.astrology.ru import SIGNS_RU, aspect_ru, planet_ru, sign_ru
from astrobot.lunar import compute_phases

_FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED
_SIGN_CODES = list(SIGNS_RU)  # already in zodiac order: Ari … Pis

# planet -> (swisseph id, ingress weight, retrograde-station weight).
# Weight = how newsworthy the event is; the picker takes the heaviest one.
_PLANETS: dict[str, tuple[int, int, int]] = {
    # The Moon changes sign every ~2.5 days — a low-weight but always-available
    # subject, so a scan window is never empty and two posts in a row can differ.
    "Moon": (swe.MOON, 20, 0),
    "Sun": (swe.SUN, 60, 0),  # never retrogrades
    "Mercury": (swe.MERCURY, 45, 90),
    "Venus": (swe.VENUS, 55, 80),
    "Mars": (swe.MARS, 55, 80),
    "Jupiter": (swe.JUPITER, 85, 80),
    "Saturn": (swe.SATURN, 85, 80),
    # Outer planets: an ingress is once-a-generation news, a station is yearly.
    "Uranus": (swe.URANUS, 95, 65),
    "Neptune": (swe.NEPTUNE, 95, 60),
    "Pluto": (swe.PLUTO, 95, 60),
}

_MOON_PHASE_WEIGHT = {"new": 100, "full": 95}

# ─── transiting aspects (planet to planet in the sky) ─────────────────────────

# Directional separations to watch, mapped to the aspect they make. Both signed
# versions of the asymmetric aspects are listed because the difference between
# two longitudes is directional (240° = trine just as much as 120°).
_ASPECT_ANGLES: dict[int, str] = {
    0: "conjunction",
    60: "sextile",
    90: "square",
    120: "trine",
    180: "opposition",
    240: "trine",
    270: "square",
    300: "sextile",
}

# How much a planet contributes to an aspect's newsworthiness. The Moon is left
# out of aspects entirely — it makes half a dozen a day, which is pure noise.
_IMPORTANCE: dict[str, int] = {
    "Sun": 3, "Mercury": 1, "Venus": 2, "Mars": 3,
    "Jupiter": 4, "Saturn": 5, "Uranus": 5, "Neptune": 5, "Pluto": 6,
}
_ASPECT_BONUS = {"conjunction": 8, "opposition": 8, "square": 6, "trine": 4, "sextile": 2}

# Above this, a day-to-day jump in the separation is a 0°/360° wrap rather than a
# real crossing. The fastest tracked pair (Mercury vs Venus) moves ~3.5°/day.
_WRAP_GUARD = 20.0


@dataclass(frozen=True)
class AstroEvent:
    key: str      # dedup key, e.g. "retro:Mercury:2026-08-20"
    when: date
    title: str    # short headline, goes into the campaign name
    detail: str   # the raw fact handed to the LLM
    weight: int


def _sign_of(lon: float) -> str:
    return sign_ru(_SIGN_CODES[int(lon // 30) % 12])


def _positions(d: date) -> dict[str, tuple[float, float]]:
    """Longitude and daily speed of every tracked body at noon UT."""
    jd = swe.julday(d.year, d.month, d.day, 12.0)
    out: dict[str, tuple[float, float]] = {}
    for name, (pid, _, _) in _PLANETS.items():
        xx = swe.calc_ut(jd, pid, _FLAGS)[0]
        out[name] = (xx[0], xx[3])
    return out


def _moon_sign(d: date) -> str:
    jd = swe.julday(d.year, d.month, d.day, 12.0)
    return _sign_of(swe.calc_ut(jd, swe.MOON, _FLAGS)[0][0])


_ASPECT_PAIRS = [
    (a, b)
    for i, a in enumerate(_IMPORTANCE)
    for b in list(_IMPORTANCE)[i + 1:]
]


def _offset(diff: float, angle: int) -> float:
    """How far the (directional) separation is from an exact aspect, in
    [-180, 180). Zero exactly on the aspect, and it changes sign as the aspect
    perfects — which is what the day-stepping scan looks for."""
    return (diff - angle + 180.0) % 360.0 - 180.0


def _aspect_events(prev: dict, cur: dict, d: date) -> list[AstroEvent]:
    """Aspects between two planets that become exact on day `d`."""
    out: list[AstroEvent] = []
    for a, b in _ASPECT_PAIRS:
        pdiff = (prev[a][0] - prev[b][0]) % 360.0
        cdiff = (cur[a][0] - cur[b][0]) % 360.0
        for angle, aspect in _ASPECT_ANGLES.items():
            po, co = _offset(pdiff, angle), _offset(cdiff, angle)
            if abs(po - co) > _WRAP_GUARD or (po > 0) == (co > 0):
                continue
            name = aspect_ru(aspect)
            weight = (
                40
                + 3 * (_IMPORTANCE[a] + _IMPORTANCE[b])
                + _ASPECT_BONUS[aspect]
            )
            out.append(
                AstroEvent(
                    key=f"aspect:{a}:{aspect}:{b}:{d.isoformat()}",
                    when=d,
                    title=f"{planet_ru(a)} — {name} — {planet_ru(b)}",
                    detail=(
                        f"{planet_ru(a)} в знаке {_sign_of(cur[a][0])} образует точный "
                        f"аспект «{name}» к планете {planet_ru(b)} "
                        f"в знаке {_sign_of(cur[b][0])}."
                    ),
                    weight=weight,
                )
            )
    return out


def scan_events(start: date, end: date) -> list[AstroEvent]:
    """All notable sky events in [start, end]: lunations, ingresses, retrograde
    stations and every planet-to-planet aspect that perfects in the window. A
    window of 3+ days always yields something, because the Moon changes sign
    every ~2.5 days."""
    events: list[AstroEvent] = []

    for phase in compute_phases(start, end):
        sign = _moon_sign(phase.event_date)
        label = "Новолуние" if phase.kind == "new" else "Полнолуние"
        events.append(
            AstroEvent(
                key=f"moon_phase:{phase.kind}:{phase.event_date.isoformat()}",
                when=phase.event_date,
                title=f"{label} в знаке {sign}",
                detail=f"{label} в знаке {sign}.",
                weight=_MOON_PHASE_WEIGHT[phase.kind],
            )
        )

    prev = _positions(start)
    d = start + timedelta(days=1)
    while d <= end:
        cur = _positions(d)
        for name, (_, ingress_w, station_w) in _PLANETS.items():
            plon, pspeed = prev[name]
            clon, cspeed = cur[name]
            ru = planet_ru(name)
            if int(plon // 30) != int(clon // 30):
                sign = _sign_of(clon)
                events.append(
                    AstroEvent(
                        key=f"ingress:{name}:{d.isoformat()}",
                        when=d,
                        title=f"{ru} переходит в знак {sign}",
                        detail=f"{ru} переходит в знак {sign}.",
                        weight=ingress_w,
                    )
                )
            if station_w and pspeed > 0 > cspeed:
                events.append(
                    AstroEvent(
                        key=f"retro:{name}:{d.isoformat()}",
                        when=d,
                        title=f"{ru} уходит в ретроград",
                        detail=f"{ru} становится ретроградным в знаке {_sign_of(clon)}.",
                        weight=station_w,
                    )
                )
            elif station_w and pspeed < 0 < cspeed:
                events.append(
                    AstroEvent(
                        key=f"direct:{name}:{d.isoformat()}",
                        when=d,
                        title=f"{ru} выходит из ретрограда",
                        detail=f"{ru} снова идёт прямо, в знаке {_sign_of(clon)}.",
                        weight=station_w - 5,
                    )
                )
        events.extend(_aspect_events(prev, cur, d))
        prev = cur
        d += timedelta(days=1)

    events.sort(key=lambda e: (e.when, -e.weight))
    return events


def pick_event(when: date, exclude_key: str | None = None) -> AstroEvent:
    """The most newsworthy event around `when` (yesterday … +3 days), skipping the
    one the previous post already used. Never returns None — a 5-day window always
    holds at least one Moon ingress."""
    events = scan_events(when - timedelta(days=1), when + timedelta(days=3))
    fresh = [e for e in events if e.key != exclude_key]
    if not fresh:
        # The only candidate is what the last post was about (happens when a slow
        # stretch holds a single Moon ingress) — look a week ahead rather than
        # repeat ourselves.
        events = scan_events(when, when + timedelta(days=7))
        fresh = [e for e in events if e.key != exclude_key] or events
    return max(fresh, key=lambda e: (e.weight, -abs((e.when - when).days)))
