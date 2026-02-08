"""OpenFlights CSV parser (beta)."""

import csv
import io
import uuid
from datetime import time

from app.models.flight import Flight
from app.services.parsers._base import compute_utc_times, parse_date, parse_time
from app.services.timezone_resolver import resolve_airport_code

# Single-letter enum maps
_SEAT_TYPE_MAP = {"W": "Window", "A": "Aisle", "M": "Middle"}
_CLASS_MAP = {"Y": "Economy", "P": "Premium Economy", "C": "Business", "F": "First"}
_REASON_MAP = {"B": "Business", "L": "Leisure", "C": "Crew", "O": "Other"}


def _parse_datetime_field(raw: str) -> tuple:
    """Parse date that may include embedded time: 'YYYY-MM-DD HH:MM[:SS]'.

    Returns (date, time|None).
    """
    if not raw:
        return None, None
    raw = raw.strip()
    # Split on space to check for embedded time
    parts = raw.split(" ", 1)
    d = parse_date(parts[0])
    t = None
    if len(parts) > 1:
        t = parse_time(parts[1])
    return d, t


def parse_openflights_csv(file_content: str) -> tuple[list[Flight], uuid.UUID]:
    """Parse an OpenFlights CSV export.

    Expected 19-column CSV with columns:
    Date, From, To, Flight_Number, Airline, Distance, Duration, Seat, Seat_Type,
    Class, Reason, Plane, Registration, Trip, Note, From_OID, To_OID, Airline_OID, Plane_OID
    """
    batch_id = uuid.uuid4()
    flights: list[Flight] = []

    reader = csv.DictReader(io.StringIO(file_content))

    for row_idx, row in enumerate(reader):
        # Date may include embedded time
        dep_date, embedded_dep_time = _parse_datetime_field(row.get("Date", ""))
        if dep_date is None:
            continue

        # Bare IATA/ICAO codes
        from_code = (row.get("From", "") or "").strip()
        to_code = (row.get("To", "") or "").strip()

        dep_info = resolve_airport_code(from_code)
        arr_info = resolve_airport_code(to_code)

        dep_iata = dep_info["iata"] if dep_info else from_code if len(from_code) == 3 else None
        dep_icao = dep_info["icao"] if dep_info else from_code if len(from_code) == 4 else None
        dep_city = dep_info["city"] if dep_info else None
        dep_name = dep_info["name"] if dep_info else None

        arr_iata = arr_info["iata"] if arr_info else to_code if len(to_code) == 3 else None
        arr_icao = arr_info["icao"] if arr_info else to_code if len(to_code) == 4 else None
        arr_city = arr_info["city"] if arr_info else None
        arr_name = arr_info["name"] if arr_info else None

        # Duration: "H:MM" or "HH:MM"
        duration = parse_time(row.get("Duration", ""))

        dep_utc, arr_utc, arrival_date = compute_utc_times(
            dep_date, embedded_dep_time, duration, dep_icao, arr_icao
        )

        # Map single-letter enums
        seat_type_raw = (row.get("Seat_Type", "") or "").strip()
        class_raw = (row.get("Class", "") or "").strip()
        reason_raw = (row.get("Reason", "") or "").strip()

        flight = Flight(
            import_batch_id=batch_id,
            row_index=row_idx,
            date=dep_date,
            flight_number=(row.get("Flight_Number", "") or "").strip() or None,
            departure_city=dep_city,
            departure_airport_name=dep_name,
            departure_airport_iata=dep_iata,
            departure_airport_icao=dep_icao,
            arrival_city=arr_city,
            arrival_airport_name=arr_name,
            arrival_airport_iata=arr_iata,
            arrival_airport_icao=arr_icao,
            dep_time=embedded_dep_time,
            arr_time=None,
            duration=duration,
            departure_datetime_utc=dep_utc,
            arrival_datetime_utc=arr_utc,
            arrival_date=arrival_date,
            airline=(row.get("Airline", "") or "").strip() or None,
            aircraft=(row.get("Plane", "") or "").strip() or None,
            registration=(row.get("Registration", "") or "").strip() or None,
            seat_number=(row.get("Seat", "") or "").strip() or None,
            seat_type=_SEAT_TYPE_MAP.get(seat_type_raw, seat_type_raw or None),
            flight_class=_CLASS_MAP.get(class_raw, class_raw or None),
            flight_reason=_REASON_MAP.get(reason_raw, reason_raw or None),
            note=(row.get("Note", "") or "").strip() or None,
        )
        flights.append(flight)

    return flights, batch_id
