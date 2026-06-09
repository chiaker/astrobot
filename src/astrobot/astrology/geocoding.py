from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

import structlog
from geopy.adapters import AioHTTPAdapter
from geopy.exc import GeocoderTimedOut
from geopy.geocoders import Nominatim
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from timezonefinder import TimezoneFinder

from astrobot.db.models import GeocodeCache

log = structlog.get_logger(__name__)

_USER_AGENT = "astrobot/0.1 (telegram astrology bot)"
_tz_finder = TimezoneFinder()


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    display_name: str
    tz: str


def _resolve_tz(lat: float, lon: float) -> str:
    tz = _tz_finder.timezone_at(lat=lat, lng=lon)
    if not tz:
        raise ValueError(f"timezone not found for ({lat}, {lon})")
    return tz


async def geocode_city(session: AsyncSession, query: str) -> GeocodeResult | None:
    query = query.strip()
    if not query:
        return None

    cached = await session.scalar(
        select(GeocodeCache).where(GeocodeCache.query == query.lower())
    )
    if cached:
        return GeocodeResult(
            lat=cached.lat,
            lon=cached.lon,
            display_name=cached.display_name,
            tz=cached.tz,
        )

    try:
        async with Nominatim(user_agent=_USER_AGENT, adapter_factory=AioHTTPAdapter) as geo:
            location = await geo.geocode(query, language="ru", timeout=10)
    except GeocoderTimedOut:
        log.warning("geocode_timeout", query=query)
        return None

    if location is None:
        return None

    tz = _resolve_tz(location.latitude, location.longitude)
    result = GeocodeResult(
        lat=location.latitude,
        lon=location.longitude,
        display_name=location.address,
        tz=tz,
    )

    session.add(
        GeocodeCache(
            query=query.lower(),
            lat=result.lat,
            lon=result.lon,
            display_name=result.display_name,
            tz=result.tz,
            fetched_at=datetime.utcnow(),
        )
    )
    await session.commit()
    return result


def resolve_tz_for_coords(lat: float, lon: float) -> str:
    return _resolve_tz(lat, lon)


async def _example():  # pragma: no cover
    await asyncio.sleep(0)
