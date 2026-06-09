from __future__ import annotations

from astrobot.astrology.ru import aspect_ru, fmt_degrees, planet_ru, sign_ru
from astrobot.astrology.types import ChartData


def chart_to_markdown(chart: ChartData) -> str:
    """Compact RU markdown of a natal chart, fed as cached LLM context."""
    b = chart.birth
    time_str = "неизвестно" if b.time_unknown else b.time.strftime("%H:%M")

    lines: list[str] = []
    lines.append("# Натальная карта")
    lines.append("")
    lines.append(f"- Дата: {b.date.strftime('%d.%m.%Y')}")
    lines.append(f"- Время: {time_str}")
    lines.append(f"- Место: {b.city_name} ({b.lat:.4f}, {b.lon:.4f}, {b.tz})")
    if b.time_unknown:
        lines.append(
            "- Внимание: время рождения неизвестно. Дома и Асцендент "
            "не используются. Луна также может быть в соседнем градусе."
        )
    lines.append("")

    lines.append("## Планеты")
    for planet in chart.planets.values():
        ru_name = planet_ru(planet.name)
        ru_sign = sign_ru(planet.sign)
        deg = fmt_degrees(planet.degrees_in_sign)
        retro = " R" if planet.retrograde else ""
        house = f", дом {planet.house}" if planet.house else ""
        lines.append(f"- {ru_name}: {ru_sign} {deg}{retro}{house}")

    if chart.has_houses and chart.angles:
        lines.append("")
        lines.append("## Углы")
        for angle in chart.angles.values():
            ru_name = planet_ru(angle.name)
            ru_sign = sign_ru(angle.sign)
            deg = fmt_degrees(angle.degrees_in_sign)
            lines.append(f"- {ru_name}: {ru_sign} {deg}")

    if chart.aspects:
        lines.append("")
        lines.append("## Аспекты")
        for asp in chart.aspects:
            p1 = planet_ru(asp.p1)
            p2 = planet_ru(asp.p2)
            kind = aspect_ru(asp.kind)
            lines.append(f"- {p1} — {kind} — {p2} (орб {asp.orb:.1f}°)")

    return "\n".join(lines)
