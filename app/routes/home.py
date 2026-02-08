from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.flight import Flight
from app.models.photo import CandidatePhoto, FlightPhotoMatch, UserDecision
from app.models.scrape_job import ScrapeJob

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    # Core counts
    total_flights = (await db.execute(select(func.count(Flight.id)))).scalar() or 0
    total_photos = (await db.execute(select(func.count(CandidatePhoto.id)))).scalar() or 0
    total_matches = (await db.execute(select(func.count(FlightPhotoMatch.id)))).scalar() or 0

    # Decision counts
    approved = (
        await db.execute(
            select(func.count(UserDecision.id)).where(UserDecision.decision == "approved")
        )
    ).scalar() or 0
    rejected = (
        await db.execute(
            select(func.count(UserDecision.id)).where(UserDecision.decision == "rejected")
        )
    ).scalar() or 0

    # Pending review = matches without a decision
    decided_subq = select(UserDecision.match_id)
    pending_review = (
        await db.execute(
            select(func.count(FlightPhotoMatch.id)).where(
                FlightPhotoMatch.id.notin_(decided_subq)
            )
        )
    ).scalar() or 0

    # Unique aircraft and airlines
    unique_aircraft = (
        await db.execute(
            select(func.count(func.distinct(Flight.aircraft))).where(Flight.aircraft.isnot(None))
        )
    ).scalar() or 0
    unique_airlines = (
        await db.execute(
            select(func.count(func.distinct(Flight.airline))).where(Flight.airline.isnot(None))
        )
    ).scalar() or 0

    # Date range
    date_range = (
        await db.execute(select(func.min(Flight.date), func.max(Flight.date)))
    ).one_or_none()
    first_date = date_range[0] if date_range else None
    last_date = date_range[1] if date_range else None

    # Scraper activity
    running_jobs = (
        await db.execute(
            select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "running")
        )
    ).scalar() or 0
    pending_jobs = (
        await db.execute(
            select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "pending")
        )
    ).scalar() or 0

    # Recent flights
    recent_q = await db.execute(
        select(Flight).order_by(Flight.date.desc(), Flight.dep_time.desc()).limit(5)
    )
    recent_flights = recent_q.scalars().all()

    has_data = total_flights > 0

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "has_data": has_data,
            "total_flights": total_flights,
            "total_photos": total_photos,
            "total_matches": total_matches,
            "approved": approved,
            "rejected": rejected,
            "pending_review": pending_review,
            "unique_aircraft": unique_aircraft,
            "unique_airlines": unique_airlines,
            "first_date": first_date,
            "last_date": last_date,
            "running_jobs": running_jobs,
            "pending_jobs": pending_jobs,
            "recent_flights": recent_flights,
        },
    )
