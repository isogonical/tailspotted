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
