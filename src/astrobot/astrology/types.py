from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time


@dataclass(frozen=True)
class BirthData:
    name: str
    date: date
    time: time
    time_unknown: bool
    lat: float
    lon: float
    tz: str
    city_name: str


@dataclass
class PlanetPosition:
    name: str
    sign: str
    degrees_in_sign: float
    abs_position: float
    house: int | None
    retrograde: bool


@dataclass
class Aspect:
    p1: str
    p2: str
    kind: str
    orb: float


@dataclass
class ChartData:
    birth: BirthData
    planets: dict[str, PlanetPosition]
    angles: dict[str, PlanetPosition] = field(default_factory=dict)
    house_cusps: list[float] | None = None
    aspects: list[Aspect] = field(default_factory=list)

    @property
    def has_houses(self) -> bool:
        return self.house_cusps is not None and not self.birth.time_unknown
