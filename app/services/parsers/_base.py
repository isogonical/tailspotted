"""Shared helpers for flight log parsers."""

from datetime import date, datetime, time, timedelta
from typing import NamedTuple

from zoneinfo import ZoneInfo

from app.services.timezone_resolver import get_timezone

_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y"]


class FormatInfo(NamedTuple):
    name: str
    beta: bool
    file_type: str  # "csv" or "json"


def parse_date(raw: str) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(raw: str) -> time | None:
    if not raw:
        return None
    parts = raw.strip().split(":")
    if len(parts) >= 2:
        return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    return None


def compute_utc_times(
    dep_date: date | None,
    dep_time: time | None,
    duration: time | None,
    dep_icao: str | None,
    arr_icao: str | None,
) -> tuple[datetime | None, datetime | None, date | None]:
    """Compute UTC departure/arrival datetimes and local arrival date."""
    if not dep_date or not dep_time or not duration:
        return None, None, None

    dep_tz_name = get_timezone(dep_icao) if dep_icao else None
    arr_tz_name = get_timezone(arr_icao) if arr_icao else None

    if not dep_tz_name:
        return None, None, None

    dep_tz = ZoneInfo(dep_tz_name)
    dep_dt = datetime.combine(dep_date, dep_time, tzinfo=dep_tz)
    dur_delta = timedelta(hours=duration.hour, minutes=duration.minute, seconds=duration.second)
    arr_dt_utc = dep_dt + dur_delta

    if arr_tz_name:
        arr_tz = ZoneInfo(arr_tz_name)
        arr_local = arr_dt_utc.astimezone(arr_tz)
        arrival_date = arr_local.date()
    else:
        arrival_date = arr_dt_utc.date()

    return dep_dt, arr_dt_utc, arrival_date


def compute_utc_from_datetimes(
    dep_dt_utc: datetime | None,
    arr_dt_utc: datetime | None,
    arr_icao: str | None,
) -> tuple[datetime | None, datetime | None, date | None]:
    """Derive arrival date from pre-computed UTC datetimes."""
    if not dep_dt_utc or not arr_dt_utc:
        return dep_dt_utc, arr_dt_utc, None

    arr_tz_name = get_timezone(arr_icao) if arr_icao else None
    if arr_tz_name:
        arr_tz = ZoneInfo(arr_tz_name)
        arr_local = arr_dt_utc.astimezone(arr_tz)
        arrival_date = arr_local.date()
    else:
        arrival_date = arr_dt_utc.date()

    return dep_dt_utc, arr_dt_utc, arrival_date
