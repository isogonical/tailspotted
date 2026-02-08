import airportsdata

_airports_icao = airportsdata.load("ICAO")
_airports_iata = airportsdata.load("IATA")

# Legacy alias
_airports = _airports_icao


def get_timezone(icao_code: str) -> str | None:
    """Return IANA timezone string for an ICAO airport code."""
    airport = _airports_icao.get(icao_code)
    if airport:
        return airport.get("tz")
    return None


def to_iata(code: str) -> str:
    """Convert any airport code (ICAO or IATA) to IATA. Returns original if not found."""
    if not code:
        return code
    code = code.upper().strip()
    # Already IATA (3 chars)?
    if len(code) == 3 and code in _airports_iata:
        return code
    # ICAO (4 chars) → IATA
    if code in _airports_icao:
        iata = _airports_icao[code].get("iata", "")
        return iata if iata else code
    return code


def resolve_airport_code(code: str) -> dict | None:
    """Auto-detect IATA (3 chars) vs ICAO (4 chars) and return airport info.

    Returns dict with keys: iata, icao, city, name, tz — or None if not found.
    """
    if not code:
        return None
    code = code.upper().strip()

    airport = None
    if len(code) == 4 and code in _airports_icao:
        airport = _airports_icao[code]
    elif len(code) == 3 and code in _airports_iata:
        airport = _airports_iata[code]

    if not airport:
        return None

    return {
        "iata": airport.get("iata", ""),
        "icao": airport.get("icao", ""),
        "city": airport.get("city", ""),
        "name": airport.get("name", ""),
        "tz": airport.get("tz", ""),
    }
