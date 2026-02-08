"""AirTrail JSON parser (beta)."""

import json
import uuid
from datetime import datetime, time, timedelta, timezone

from app.models.flight import Flight
from app.services.parsers._base import compute_utc_from_datetimes
from app.services.timezone_resolver import resolve_airport_code

# Class normalization
_CLASS_MAP = {
    "economy": "Economy",
    "premium_economy": "Premium Economy",
    "business": "Business",
    "first": "First",
}

_SEAT_TYPE_MAP = {
    "window": "Window",
    "middle": "Middle",
    "aisle": "Aisle",
}


def _str_val(val) -> str:
    """Extract a string from a value that might be a dict, list, or primitive."""
    if val is None:
        return ""
    if isinstance(val, dict):
        return str(val.get("name") or val.get("value") or val.get("code") or "")
    if isinstance(val, (list, tuple)):
        return ""
    return str(val)


def _parse_iso_datetime(raw: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp to UTC datetime."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _duration_minutes_to_time(minutes: int | None) -> time | None:
    """Convert integer minutes to a time object (max 23:59)."""
    if minutes is None or minutes < 0:
        return None
    h, m = divmod(int(minutes), 60)
    if h >= 24:
        return time(23, 59)
    return time(h, m)


def parse_airtrail_json(file_content: str) -> tuple[list[Flight], uuid.UUID]:
    """Parse an AirTrail JSON export.

    Expected format: {"flights": [...], "users": [...]}
    Each flight has: from/to airport objects with icao, date, departureDate,
    arrivalDate, duration (minutes), seats, aircraftReg, flightNumber, airline, etc.
    """
    batch_id = uuid.uuid4()
    flights: list[Flight] = []

    data = json.loads(file_content)
    flight_list = data.get("flights", [])

    # Handle legacy format: flights might be a dict keyed by ID
    if isinstance(flight_list, dict):
        flight_list = list(flight_list.values())

    for row_idx, entry in enumerate(flight_list):
        # Airport info — primary key is "icao", may also have "iata"
        from_airport = entry.get("from") or {}
        to_airport = entry.get("to") or {}

        # Handle both object format and string format
        if isinstance(from_airport, str):
            from_code = from_airport
            dep_info = resolve_airport_code(from_code)
        else:
            from_code = from_airport.get("icao") or from_airport.get("iata", "")
            dep_info = resolve_airport_code(from_code) if from_code else None

        if isinstance(to_airport, str):
            to_code = to_airport
            arr_info = resolve_airport_code(to_code)
        else:
            to_code = to_airport.get("icao") or to_airport.get("iata", "")
            arr_info = resolve_airport_code(to_code) if to_code else None

        dep_iata = dep_info["iata"] if dep_info else None
        dep_icao = dep_info["icao"] if dep_info else None
        dep_city = dep_info["city"] if dep_info else None
        dep_name = dep_info["name"] if dep_info else None

        arr_iata = arr_info["iata"] if arr_info else None
        arr_icao = arr_info["icao"] if arr_info else None
        arr_city = arr_info["city"] if arr_info else None
        arr_name = arr_info["name"] if arr_info else None

        # Dates and times — ISO 8601 timestamps
        dep_dt_utc = _parse_iso_datetime(entry.get("departureDate"))
        arr_dt_utc = _parse_iso_datetime(entry.get("arrivalDate"))

        # Fallback: "date" field (date-only)
        date_raw = entry.get("date", "")
        if dep_dt_utc:
            dep_date = dep_dt_utc.date()
        elif date_raw:
            try:
                dep_date = datetime.fromisoformat(date_raw).date()
            except (ValueError, TypeError):
                dep_date = None
        else:
            dep_date = None

        if dep_date is None:
            continue

        # Extract local times
        dep_time_val = dep_dt_utc.time() if dep_dt_utc else None
        arr_time_val = arr_dt_utc.time() if arr_dt_utc else None

        # Duration
        duration_min = entry.get("duration")
        if isinstance(duration_min, (int, float)) and duration_min > 0:
            duration = _duration_minutes_to_time(int(duration_min))
            # If we have departure but no arrival, compute arrival
            if dep_dt_utc and not arr_dt_utc:
                arr_dt_utc = dep_dt_utc + timedelta(minutes=int(duration_min))
        else:
            duration = None

        dep_dt_utc, arr_dt_utc, arrival_date = compute_utc_from_datetimes(
            dep_dt_utc, arr_dt_utc, arr_icao
        )

        # Seat info — from first entry in "seats" array
        seats = entry.get("seats") or []
        seat_number = None
        seat_type = None
        flight_class = None
        if seats and isinstance(seats, list) and len(seats) > 0:
            seat = seats[0]
            if isinstance(seat, dict):
                seat_number = seat.get("seat") or seat.get("number")
                seat_type_raw = (seat.get("type") or seat.get("seatType") or "").lower()
                seat_type = _SEAT_TYPE_MAP.get(seat_type_raw, seat_type_raw.title() or None) if seat_type_raw else None
                class_raw = (seat.get("class") or seat.get("seatClass") or "").lower()
                flight_class = _CLASS_MAP.get(class_raw, class_raw.title() or None) if class_raw else None

        # Top-level class fallback
        if not flight_class:
            class_raw = (entry.get("class") or entry.get("seatClass") or "").lower()
            flight_class = _CLASS_MAP.get(class_raw, class_raw.title() or None) if class_raw else None

        flight = Flight(
            import_batch_id=batch_id,
            row_index=row_idx,
            date=dep_date,
            flight_number=_str_val(entry.get("flightNumber") or entry.get("flight_number")).strip() or None,
            departure_city=dep_city,
            departure_airport_name=dep_name,
            departure_airport_iata=dep_iata,
            departure_airport_icao=dep_icao,
            arrival_city=arr_city,
            arrival_airport_name=arr_name,
            arrival_airport_iata=arr_iata,
            arrival_airport_icao=arr_icao,
            dep_time=dep_time_val,
            arr_time=arr_time_val,
            duration=duration,
            departure_datetime_utc=dep_dt_utc,
            arrival_datetime_utc=arr_dt_utc,
            arrival_date=arrival_date,
            airline=_str_val(entry.get("airline")).strip() or None,
            aircraft=_str_val(entry.get("aircraft") or entry.get("aircraftType")).strip() or None,
            registration=_str_val(entry.get("aircraftReg") or entry.get("registration")).strip() or None,
            seat_number=str(seat_number).strip() if seat_number else None,
            seat_type=seat_type,
            flight_class=flight_class,
            flight_reason=None,
            note=_str_val(entry.get("note") or entry.get("notes")).strip() or None,
        )
        flights.append(flight)

    return flights, batch_id
