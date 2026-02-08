from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.flight import Flight
from app.models.photo import CandidatePhoto, FlightPhotoMatch, UserDecision

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/library", response_class=HTMLResponse)
async def library(
    request: Request,
    year: int | None = None,
    route: str | None = None,
    status: str = "approved",
    db: AsyncSession = Depends(get_db),
):
    if status not in ("approved", "rejected"):
        status = "approved"

    query = (
        select(FlightPhotoMatch, CandidatePhoto, Flight, UserDecision)
        .join(CandidatePhoto, FlightPhotoMatch.photo_id == CandidatePhoto.id)
        .join(Flight, FlightPhotoMatch.flight_id == Flight.id)
        .join(UserDecision, UserDecision.match_id == FlightPhotoMatch.id)
        .where(UserDecision.decision == status)
        .order_by(Flight.date.desc())
    )

    if year:
        query = query.where(func.extract("year", Flight.date) == year)

    if route:
        parts = route.split("-")
        if len(parts) == 2:
            query = query.where(
                Flight.departure_airport_iata == parts[0],
                Flight.arrival_airport_iata == parts[1],
            )

    result = await db.execute(query)
    rows = result.all()

    # Get available years for filter (scoped to current status)
    years_q = await db.execute(
        select(func.distinct(func.extract("year", Flight.date)))
        .join(FlightPhotoMatch, FlightPhotoMatch.flight_id == Flight.id)
        .join(UserDecision, UserDecision.match_id == FlightPhotoMatch.id)
        .where(UserDecision.decision == status)
        .order_by(func.extract("year", Flight.date).desc())
    )
    years = [int(y) for y in years_q.scalars().all()]

    items = []
    for match, photo, flight, decision in rows:
        items.append({
            "match": match,
            "photo": photo,
            "flight": flight,
            "decision": decision,
        })

    return templates.TemplateResponse(
        "library.html",
        {
            "request": request,
            "items": items,
            "years": years,
            "selected_year": year,
            "selected_route": route,
            "selected_status": status,
        },
    )


@router.post("/library/{match_id}/requeue", response_class=HTMLResponse)
async def requeue_match(
    match_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete the user decision so the match goes back into the review queue."""
    await db.execute(
        delete(UserDecision).where(UserDecision.match_id == match_id)
    )
    await db.commit()
    # Return empty string so HTMX removes the card from the grid
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = "reviewCountChanged"
    return resp
