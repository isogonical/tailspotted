"""Backward-compatibility shim — delegates to parsers.fr24."""

from app.services.parsers.fr24 import parse_fr24_csv


def parse_csv(file_content: str | bytes):
    """Parse a FlightRadar24 CSV export. Legacy entry point."""
    if isinstance(file_content, bytes):
        file_content = file_content.decode("utf-8-sig")
    return parse_fr24_csv(file_content)
