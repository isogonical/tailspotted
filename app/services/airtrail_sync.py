"""AirTrail direct sync — fetch flights from AirTrail REST API."""

import uuid
from datetime import datetime, timedelta

import httpx

from app.models.flight import Flight
from app.services.parsers._base import FormatInfo, compute_utc_from_datetimes
from app.services.parsers.airtrail import (
    _CLASS_MAP,
    _SEAT_TYPE_MAP,
    _duration_minutes_to_time,
    _parse_iso_datetime,
)
from app.services.timezone_resolver import resolve_airport_code

_TIMEOUT = 15.0


async def test_airtrail_connection(url: str, api_key: str) -> tuple[bool, str]:
    """Test connectivity to an AirTrail instance.

    Returns (success, message).
    """
    url = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{url}/api/flight/list",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                count = len(data)
            elif isinstance(data, dict):
                flights = data.get("flights", data)
                count = len(flights) if isinstance(flights, (list, dict)) else 0
            else:
                count = 0
            return True, f"Connected — {count} flights available"
        if resp.status_code == 401:
            return False, "Authentication failed — check your API key"
        return False, f"Unexpected status {resp.status_code}"
    except httpx.ConnectError:
        return False, f"Cannot connect to {url}"
    except httpx.TimeoutException:
        return False, "Connection timed out"
    except Exception as e:
        return False, str(e)


async def sync_airtrail_flights(
    url: str, api_key: str
) -> tuple[list[Flight], uuid.UUID, FormatInfo]:
    """Fetch all flights from AirTrail API and convert to Flight models.

    Returns (flights, batch_id, format_info).
    """
    url = url.rstrip("/")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{url}/api/flight/list",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()

    data = resp.json()
    if isinstance(data, dict):
        flight_list = data.get("flights", [])
        if isinstance(flight_list, dict):
            flight_list = list(flight_list.values())
    elif isinstance(data, list):
        flight_list = data
    else:
        flight_list = []

    batch_id = uuid.uuid4()
    flights: list[Flight] = []

    for row_idx, entry in enumerate(flight_list):
        from_airport = entry.get("from") or {}
        to_airport = entry.get("to") or {}

        if isinstance(from_airport, str):
            dep_info = resolve_airport_code(from_airport)
        else:
            from_code = from_airport.get("icao") or from_airport.get("iata", "")
            dep_info = resolve_airport_code(from_code) if from_code else None

        if isinstance(to_airport, str):
            arr_info = resolve_airport_code(to_airport)
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

        dep_dt_utc = _parse_iso_datetime(entry.get("departureDate"))
        arr_dt_utc = _parse_iso_datetime(entry.get("arrivalDate"))

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

        dep_time_val = dep_dt_utc.time() if dep_dt_utc else None
        arr_time_val = arr_dt_utc.time() if arr_dt_utc else None

        duration_min = entry.get("duration")
        if isinstance(duration_min, (int, float)) and duration_min > 0:
            duration = _duration_minutes_to_time(int(duration_min))
            if dep_dt_utc and not arr_dt_utc:
                arr_dt_utc = dep_dt_utc + timedelta(minutes=int(duration_min))
        else:
            duration = None

        dep_dt_utc, arr_dt_utc, arrival_date = compute_utc_from_datetimes(
            dep_dt_utc, arr_dt_utc, arr_icao
        )

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

        if not flight_class:
            class_raw = (entry.get("class") or entry.get("seatClass") or "").lower()
            flight_class = _CLASS_MAP.get(class_raw, class_raw.title() or None) if class_raw else None

        flight = Flight(
            import_batch_id=batch_id,
            row_index=row_idx,
            date=dep_date,
            flight_number=(entry.get("flightNumber") or entry.get("flight_number") or "").strip() or None,
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
            airline=(entry.get("airline") or "").strip() or None,
            aircraft=(entry.get("aircraft") or entry.get("aircraftType") or "").strip() or None,
            registration=(entry.get("aircraftReg") or entry.get("registration") or "").strip() or None,
            seat_number=str(seat_number).strip() if seat_number else None,
            seat_type=seat_type,
            flight_class=flight_class,
            flight_reason=None,
            note=(entry.get("note") or entry.get("notes") or "").strip() or None,
        )
        flights.append(flight)

    format_info = FormatInfo("AirTrail Sync", beta=True, file_type="api")
    return flights, batch_id, format_info
