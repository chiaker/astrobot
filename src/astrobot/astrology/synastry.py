from __future__ import annotations

from dataclasses import dataclass

from kerykeion import AspectsFactory

from astrobot.astrology.chart import build_subject
from astrobot.astrology.ru import aspect_ru, planet_ru
from astrobot.astrology.types import BirthData

KEY_PLANETS: frozenset[str] = frozenset(
    {"Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn"}
)
ANGLES: frozenset[str] = frozenset({"Ascendant", "Medium_Coeli"})
MAJOR_ASPECTS: frozenset[str] = frozenset(
    {"conjunction", "opposition", "trine", "square", "sextile"}
)
_SYNASTRY_ORB = 4.0
_MAX_ASPECTS = 18


@dataclass
class SynastryAspect:
    planet_a: str  # person A's planet
    planet_b: str  # person B's planet
    aspect: str
    orb: float


@dataclass
class SynastryReport:
    aspects: list[SynastryAspect]


def _targets(time_unknown: bool) -> frozenset[str]:
    return KEY_PLANETS | (frozenset() if time_unknown else ANGLES)


def build_synastry_report(birth_a: BirthData, birth_b: BirthData) -> SynastryReport:
    """Cross-aspects between two people's charts (synastry)."""
    subj_a = build_subject(birth_a)
    subj_b = build_subject(birth_b)
    dual = AspectsFactory.dual_chart_aspects(subj_a, subj_b)

    targets_a = _targets(birth_a.time_unknown)
    targets_b = _targets(birth_b.time_unknown)

    selected: list[SynastryAspect] = []
    for asp in dual.aspects:
        if asp.aspect not in MAJOR_ASPECTS:
            continue
        if asp.p1_owner != subj_a.name or asp.p2_owner != subj_b.name:
            continue
        if asp.p1_name not in targets_a or asp.p2_name not in targets_b:
            continue
        if asp.orbit > _SYNASTRY_ORB:
            continue
        selected.append(
            SynastryAspect(
                planet_a=asp.p1_name,
                planet_b=asp.p2_name,
                aspect=asp.aspect,
                orb=float(asp.orbit),
            )
        )

    selected.sort(key=lambda a: a.orb)
    return SynastryReport(aspects=selected[:_MAX_ASPECTS])


def synastry_to_markdown(report: SynastryReport, name_a: str, name_b: str) -> str:
    lines = [f"# Синастрия: {name_a} × {name_b}", ""]
    if not report.aspects:
        lines.append(
            "Значимых межкартовых аспектов между ключевыми планетами не найдено."
        )
        return "\n".join(lines)
    lines.append("## Аспекты между картами (планета А — аспект — планета Б)")
    for a in report.aspects:
        pa = planet_ru(a.planet_a)
        pb = planet_ru(a.planet_b)
        asp = aspect_ru(a.aspect)
        lines.append(
            f"- {name_a}: {pa} — {asp} — {pb}: {name_b} (орб {a.orb:.1f}°)"
        )
    return "\n".join(lines)
