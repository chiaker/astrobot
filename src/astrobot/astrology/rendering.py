from __future__ import annotations

import cairosvg
from kerykeion import KerykeionChartSVG

from astrobot.astrology.chart import build_subject
from astrobot.astrology.types import BirthData


def render_natal_png(birth: BirthData, *, output_width: int = 1800) -> bytes:
    subject = build_subject(birth)
    chart = KerykeionChartSVG(
        subject,
        chart_language="RU",
        theme="light",
    )
    svg_text = chart.makeWheelOnlyTemplate()
    return cairosvg.svg2png(
        bytestring=svg_text.encode("utf-8"),
        output_width=output_width,
    )
