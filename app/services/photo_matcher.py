from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flight import Flight
from app.models.photo import CandidatePhoto, FlightPhotoMatch


async def match_photos_for_registration(
    db: AsyncSession, registration: str
) -> list[FlightPhotoMatch]:
    """Score and create matches between photos and flights for a registration."""
    flights_q = await db.execute(
        select(Flight).where(Flight.registration == registration)
    )
    flights = flights_q.scalars().all()

    photos_q = await db.execute(
        select(CandidatePhoto).where(CandidatePhoto.registration == registration)
    )
    photos = photos_q.scalars().all()

    new_matches = []

    for photo in photos:
        for flight in flights:
            score, reasons = _compute_score(flight, photo)
            # Require both date and airport match
            if "date" not in reasons or "airport" not in reasons:
                continue

            existing = await db.execute(
                select(FlightPhotoMatch).where(
                    FlightPhotoMatch.flight_id == flight.id,
                    FlightPhotoMatch.photo_id == photo.id,
                )
            )
            if existing.scalar_one_or_none():
                continue

            match = FlightPhotoMatch(
                flight_id=flight.id,
                photo_id=photo.id,
                match_score=score,
                match_reasons=reasons,
            )
            db.add(match)
            new_matches.append(match)

    await db.commit()
    return new_matches


def _compute_score(
    flight: Flight, photo: CandidatePhoto
) -> tuple[int, dict]:
    score = 0
    reasons = {}

    # Registration match (always true since we search by reg)
    score += 30
    reasons["registration"] = True

    # Date match
    if photo.photo_date:
        if photo.photo_date == flight.date or (
            flight.arrival_date and photo.photo_date == flight.arrival_date
        ):
            score += 40
            reasons["date"] = "exact"
        elif flight.date and abs((photo.photo_date - flight.date).days) <= 1:
            score += 20
            reasons["date"] = "adjacent"
        elif flight.arrival_date and abs((photo.photo_date - flight.arrival_date).days) <= 1:
            score += 20
            reasons["date"] = "adjacent"

    # Airport match
    if photo.airport_code:
        code = photo.airport_code.upper()
        dep = (flight.departure_airport_iata or "").upper()
        arr = (flight.arrival_airport_iata or "").upper()
        dep_icao = (flight.departure_airport_icao or "").upper()
        arr_icao = (flight.arrival_airport_icao or "").upper()
        if code in (dep, arr, dep_icao, arr_icao):
            score += 30
            reasons["airport"] = code

    return score, reasons
