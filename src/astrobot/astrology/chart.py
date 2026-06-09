from __future__ import annotations

from kerykeion import AspectsFactory, AstrologicalSubjectFactory

from astrobot.astrology.types import Aspect, BirthData, ChartData, PlanetPosition

PLANET_ATTRS: tuple[str, ...] = (
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
)

ANGLE_ATTRS: tuple[str, ...] = ("ascendant", "medium_coeli")


def _point_to_position(point, house: int | None) -> PlanetPosition:
    return PlanetPosition(
        name=point.name,
        sign=point.sign,
        degrees_in_sign=float(point.position),
        abs_position=float(point.abs_pos),
        house=house,
        retrograde=bool(getattr(point, "retrograde", False)),
    )


_HOUSE_NAME_TO_NUM: dict[str, int] = {
    "First": 1,
    "Second": 2,
    "Third": 3,
    "Fourth": 4,
    "Fifth": 5,
    "Sixth": 6,
    "Seventh": 7,
    "Eighth": 8,
    "Ninth": 9,
    "Tenth": 10,
    "Eleventh": 11,
    "Twelfth": 12,
}


def _resolve_house(point) -> int | None:
    raw = getattr(point, "house", None) or getattr(point, "house_name", None)
    if not raw:
        return None
    s = str(raw).strip()
    word = s.split("_", 1)[0]
    if word in _HOUSE_NAME_TO_NUM:
        return _HOUSE_NAME_TO_NUM[word]
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else None


def build_subject(birth: BirthData):
    return AstrologicalSubjectFactory.from_birth_data(
        name=birth.name or "User",
        year=birth.date.year,
        month=birth.date.month,
        day=birth.date.day,
        hour=birth.time.hour,
        minute=birth.time.minute,
        lat=birth.lat,
        lng=birth.lon,
        tz_str=birth.tz,
        city=birth.city_name,
        online=False,
        houses_system_identifier="P",
    )


def build_natal_chart(birth: BirthData) -> ChartData:
    subject = build_subject(birth)
    use_houses = not birth.time_unknown

    planets: dict[str, PlanetPosition] = {}
    for attr in PLANET_ATTRS:
        point = getattr(subject, attr)
        house = _resolve_house(point) if use_houses else None
        planets[point.name] = _point_to_position(point, house)

    angles: dict[str, PlanetPosition] = {}
    house_cusps: list[float] | None = None
    if use_houses:
        for attr in ANGLE_ATTRS:
            point = getattr(subject, attr)
            angles[point.name] = _point_to_position(point, _resolve_house(point))
        house_attr_names = (
            "first_house",
            "second_house",
            "third_house",
            "fourth_house",
            "fifth_house",
            "sixth_house",
            "seventh_house",
            "eighth_house",
            "ninth_house",
            "tenth_house",
            "eleventh_house",
            "twelfth_house",
        )
        houses = [getattr(subject, name, None) for name in house_attr_names]
        house_cusps = [float(h.abs_pos) for h in houses if h is not None]

    aspects = _extract_aspects(subject)

    return ChartData(
        birth=birth,
        planets=planets,
        angles=angles,
        house_cusps=house_cusps,
        aspects=aspects,
    )


def _extract_aspects(subject) -> list[Aspect]:
    model = AspectsFactory.single_chart_aspects(subject)
    raw = getattr(model, "aspects", None) or getattr(model, "all_aspects", []) or []
    result: list[Aspect] = []
    for asp in raw:
        p1 = getattr(asp, "p1_name", None) or getattr(asp, "first_planet", None)
        p2 = getattr(asp, "p2_name", None) or getattr(asp, "second_planet", None)
        kind = getattr(asp, "aspect_name", None) or getattr(asp, "aspect", None)
        orb = getattr(asp, "orbit", None)
        if orb is None:
            orb = getattr(asp, "orb", 0.0)
        if not (p1 and p2 and kind):
            continue
        result.append(Aspect(p1=str(p1), p2=str(p2), kind=str(kind), orb=float(orb)))
    return result
