"""JetLovers CSV parser (beta)."""

import csv
import io
import uuid

from app.models.flight import Flight
from app.services.parsers._base import parse_date
from app.services.timezone_resolver import resolve_airport_code

# Full-word enum normalization
_CLASS_MAP = {
    "economy": "Economy",
    "premium economy": "Premium Economy",
    "premium": "Premium Economy",
    "business": "Business",
    "first": "First",
}

_SEAT_TYPE_MAP = {
    "window": "Window",
    "middle": "Middle",
    "aisle": "Aisle",
}

_REASON_MAP = {
    "business": "Business",
    "leisure": "Leisure",
    "personal": "Personal",
    "crew": "Crew",
}


def parse_jetlovers_csv(file_content: str) -> tuple[list[Flight], uuid.UUID]:
    """Parse a JetLovers CSV export.

    Expected 13-column CSV with columns:
    id, date, origin, destination, flight_number, airline, aircraft_type,
    aircraft_reg, seat_class, seat_type, seat_number, reason, miles
    """
    batch_id = uuid.uuid4()
    flights: list[Flight] = []

    reader = csv.DictReader(io.StringIO(file_content))

    for row_idx, row in enumerate(reader):
        dep_date = parse_date(row.get("date", ""))
        if dep_date is None:
            continue

        # Bare IATA codes
        from_code = (row.get("origin", "") or "").strip()
        to_code = (row.get("destination", "") or "").strip()

        dep_info = resolve_airport_code(from_code)
        arr_info = resolve_airport_code(to_code)

        dep_iata = dep_info["iata"] if dep_info else from_code if len(from_code) == 3 else None
        dep_icao = dep_info["icao"] if dep_info else None
        dep_city = dep_info["city"] if dep_info else None
        dep_name = dep_info["name"] if dep_info else None

        arr_iata = arr_info["iata"] if arr_info else to_code if len(to_code) == 3 else None
        arr_icao = arr_info["icao"] if arr_info else None
        arr_city = arr_info["city"] if arr_info else None
        arr_name = arr_info["name"] if arr_info else None

        # Normalize enums (full-word, case-insensitive)
        class_raw = (row.get("seat_class", "") or "").strip().lower()
        seat_type_raw = (row.get("seat_type", "") or "").strip().lower()
        reason_raw = (row.get("reason", "") or "").strip().lower()

        flight = Flight(
            import_batch_id=batch_id,
            row_index=row_idx,
            date=dep_date,
            flight_number=(row.get("flight_number", "") or "").strip() or None,
            departure_city=dep_city,
            departure_airport_name=dep_name,
            departure_airport_iata=dep_iata,
            departure_airport_icao=dep_icao,
            arrival_city=arr_city,
            arrival_airport_name=arr_name,
            arrival_airport_iata=arr_iata,
            arrival_airport_icao=arr_icao,
            dep_time=None,  # JetLovers has no time data
            arr_time=None,
            duration=None,
            departure_datetime_utc=None,
            arrival_datetime_utc=None,
            arrival_date=None,
            airline=(row.get("airline", "") or "").strip() or None,
            aircraft=(row.get("aircraft_type", "") or "").strip() or None,
            registration=(row.get("aircraft_reg", "") or "").strip() or None,
            seat_number=(row.get("seat_number", "") or "").strip() or None,
            seat_type=_SEAT_TYPE_MAP.get(seat_type_raw, seat_type_raw.title() or None) if seat_type_raw else None,
            flight_class=_CLASS_MAP.get(class_raw, class_raw.title() or None) if class_raw else None,
            flight_reason=_REASON_MAP.get(reason_raw, reason_raw.title() or None) if reason_raw else None,
            note=None,
        )
        flights.append(flight)

    return flights, batch_id
