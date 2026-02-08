"""Multi-format flight log parser dispatcher."""

import json
import uuid

from app.models.flight import Flight
from app.services.parsers._base import FormatInfo


def detect_format(content: str) -> FormatInfo:
    """Auto-detect file format from content.

    Detection order:
    1. AirTrail — valid JSON with "flights" key
    2. OpenFlights — CSV header contains "From_OID"
    3. JetLovers — CSV header contains "aircraft_reg"
    4. myFlightradar24 — CSV header contains "Dep time"
    """
    stripped = content.strip()

    # JSON check first
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and "flights" in data:
                return FormatInfo(name="AirTrail", beta=True, file_type="json")
        except (json.JSONDecodeError, ValueError):
            pass

    # CSV: inspect first non-blank line (header)
    for line in content.splitlines():
        line = line.strip()
        if line:
            header = line.lower()
            if "from_oid" in header:
                return FormatInfo(name="OpenFlights", beta=True, file_type="csv")
            if "aircraft_reg" in header:
                return FormatInfo(name="JetLovers", beta=True, file_type="csv")
            if "dep time" in header:
                return FormatInfo(name="myFlightradar24", beta=False, file_type="csv")
            break

    raise ValueError(
        "Unrecognized file format. Supported formats: "
        "myFlightradar24 CSV, OpenFlights CSV, JetLovers CSV, AirTrail JSON"
    )


def parse_flight_file(
    content: str | bytes, filename: str = ""
) -> tuple[list[Flight], uuid.UUID, FormatInfo]:
    """Parse a flight log file, auto-detecting the format.

    Returns (flights, batch_id, format_info).
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")

    fmt = detect_format(content)

    if fmt.name == "myFlightradar24":
        from app.services.parsers.fr24 import parse_fr24_csv
        flights, batch_id = parse_fr24_csv(content)
    elif fmt.name == "OpenFlights":
        from app.services.parsers.openflights import parse_openflights_csv
        flights, batch_id = parse_openflights_csv(content)
    elif fmt.name == "JetLovers":
        from app.services.parsers.jetlovers import parse_jetlovers_csv
        flights, batch_id = parse_jetlovers_csv(content)
    elif fmt.name == "AirTrail":
        from app.services.parsers.airtrail import parse_airtrail_json
        flights, batch_id = parse_airtrail_json(content)
    else:
        raise ValueError(f"No parser for format: {fmt.name}")

    return flights, batch_id, fmt
