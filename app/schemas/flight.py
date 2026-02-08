from datetime import date, datetime, time

from pydantic import BaseModel


class FlightOut(BaseModel):
    id: int
    date: date
    flight_number: str | None
    departure_airport_iata: str | None
    arrival_airport_iata: str | None
    dep_time: time | None
    arr_time: time | None
    arrival_date: date | None
    airline: str | None
    aircraft: str | None
    registration: str | None

    model_config = {"from_attributes": True}


class ImportResult(BaseModel):
    batch_id: str
    flights_imported: int
    registrations_queued: int
