from datetime import date, time

import pytest

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.types import BirthData

EINSTEIN = BirthData(
    name="Einstein",
    date=date(1879, 3, 14),
    time=time(11, 30),
    time_unknown=False,
    lat=48.3984,
    lon=9.9916,
    tz="Europe/Berlin",
    city_name="Ulm",
)


@pytest.fixture(scope="module")
def chart():
    return build_natal_chart(EINSTEIN)


def test_sun_in_pisces(chart):
    sun = chart.planets["Sun"]
    assert sun.sign == "Pis", f"expected Pisces, got {sun.sign}"
    assert 22.0 <= sun.degrees_in_sign <= 25.0


def test_moon_in_sagittarius(chart):
    moon = chart.planets["Moon"]
    assert moon.sign == "Sag", f"expected Sagittarius, got {moon.sign}"


def test_ascendant_in_cancer_or_gemini(chart):
    asc = chart.angles.get("Ascendant")
    assert asc is not None, "ascendant must be computed when time is known"
    assert asc.sign in {"Can", "Gem"}


def test_aspects_present(chart):
    assert len(chart.aspects) > 0


def test_markdown_contains_planets(chart):
    md = chart_to_markdown(chart)
    assert "Солнце" in md
    assert "Луна" in md
    assert "Рыбы" in md
    assert "Асцендент" in md


def test_time_unknown_skips_houses():
    birth = BirthData(**{**EINSTEIN.__dict__, "time_unknown": True, "time": time(12, 0)})
    chart = build_natal_chart(birth)
    assert chart.angles == {}
    assert all(p.house is None for p in chart.planets.values())
    md = chart_to_markdown(chart)
    assert "время рождения неизвестно" in md.lower()
