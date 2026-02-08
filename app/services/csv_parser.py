import csv
import io
import re
import uuid
from datetime import date, datetime, time, timedelta

from zoneinfo import ZoneInfo

from app.models.flight import Flight
from app.services.timezone_resolver import get_timezone

_AIRPORT_RE = re.compile(r"^(.+?)\s*/\s*(.+?)\s*\((\w{3})/(\w{4})\)$")

SEAT_TYPE_MAP = {"1": "Window", "2": "Middle", "3": "Aisle"}
CLASS_MAP = {"1": "Economy", "2": "Business", "3": "First"}
REASON_MAP = {"1": "Personal", "2": "Business", "3": "Crew"}


def _parse_airport(raw: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Parse 'City / Airport Name (IATA/ICAO)' → (city, name, iata, icao)."""
    if not raw:
        return None, None, None, None
    m = _AIRPORT_RE.match(raw.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3), m.group(4)
    return raw.strip(), None, None, None


def _parse_time(raw: str) -> time | None:
    if not raw:
        return None
    parts = raw.strip().split(":")
    if len(parts) >= 2:
        return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    return None


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    return datetime.strptime(raw.strip(), "%Y-%m-%d").date()


def _compute_utc_times(
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


def parse_csv(file_content: str | bytes) -> tuple[list[Flight], uuid.UUID]:
    """Parse a FlightRadar24 CSV export and return Flight objects + batch ID."""
    if isinstance(file_content, bytes):
        file_content = file_content.decode("utf-8-sig")

    batch_id = uuid.uuid4()
    flights: list[Flight] = []

    # Skip leading blank lines (FlightRadar24 exports have a blank first line)
    lines = file_content.splitlines(keepends=True)
    while lines and not lines[0].strip():
        lines.pop(0)
    cleaned = "".join(lines)

    reader = csv.DictReader(io.StringIO(cleaned))

    for row_idx, row in enumerate(reader):
        dep_date = _parse_date(row.get("Date", ""))
        dep_time = _parse_time(row.get("Dep time", ""))
        arr_time = _parse_time(row.get("Arr time", ""))
        duration = _parse_time(row.get("Duration", ""))

        dep_city, dep_name, dep_iata, dep_icao = _parse_airport(row.get("From", ""))
        arr_city, arr_name, arr_iata, arr_icao = _parse_airport(row.get("To", ""))

        dep_utc, arr_utc, arrival_date = _compute_utc_times(
            dep_date, dep_time, duration, dep_icao, arr_icao
        )

        seat_type_raw = row.get("Seat type", "")
        flight_class_raw = row.get("Flight class", "")
        flight_reason_raw = row.get("Flight reason", "")

        flight = Flight(
            import_batch_id=batch_id,
            row_index=row_idx,
            date=dep_date,
            flight_number=row.get("Flight number", "").strip() or None,
            departure_city=dep_city,
            departure_airport_name=dep_name,
            departure_airport_iata=dep_iata,
            departure_airport_icao=dep_icao,
            arrival_city=arr_city,
            arrival_airport_name=arr_name,
            arrival_airport_iata=arr_iata,
            arrival_airport_icao=arr_icao,
            dep_time=dep_time,
            arr_time=arr_time,
            duration=duration,
            departure_datetime_utc=dep_utc,
            arrival_datetime_utc=arr_utc,
            arrival_date=arrival_date,
            airline=row.get("Airline", "").strip() or None,
            aircraft=row.get("Aircraft", "").strip() or None,
            registration=row.get("Registration", "").strip() or None,
            seat_number=row.get("Seat number", "").strip() or None,
            seat_type=SEAT_TYPE_MAP.get(seat_type_raw.strip(), seat_type_raw.strip() or None),
            flight_class=CLASS_MAP.get(flight_class_raw.strip(), flight_class_raw.strip() or None),
            flight_reason=REASON_MAP.get(
                flight_reason_raw.strip(), flight_reason_raw.strip() or None
            ),
            note=row.get("Note", "").strip() or None,
        )
        flights.append(flight)

    return flights, batch_id
