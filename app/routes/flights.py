import logging
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.flight import Flight
from app.models.photo import CandidatePhoto, FlightPhotoMatch, UserDecision
from app.models.scrape_job import ScrapeJob

logger = logging.getLogger(__name__)

_SOURCE_NAMES = {
    "airlinersnet": "Airliners.net",
    "jetphotos": "JetPhotos",
    "planespotters": "Planespotters.net",
    "airplane_pictures": "Airplane-Pictures.net",
}

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["source_display"] = lambda s: _SOURCE_NAMES.get(s, s)


@router.get("/flights", response_class=HTMLResponse)
async def flights_list(
    request: Request,
    page: int = 1,
    per_page: int = 50,
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * per_page

    total_q = await db.execute(select(func.count(Flight.id)))
    total = total_q.scalar() or 0

    flights_q = await db.execute(
        select(Flight)
        .order_by(Flight.date.desc(), Flight.dep_time.desc())
        .offset(offset)
        .limit(per_page)
    )
    flights = flights_q.scalars().all()

    total_pages = (total + per_page - 1) // per_page

    # Pre-compute scrape status for all flights on this page
    regs = {f.registration for f in flights if f.registration}
    flight_ids = [f.id for f in flights]

    # Get scrape jobs grouped by registration
    scrape_by_reg = {}
    if regs:
        jobs_q = await db.execute(
            select(ScrapeJob).where(ScrapeJob.registration.in_(regs))
        )
        for job in jobs_q.scalars().all():
            scrape_by_reg.setdefault(job.registration, []).append(job)

    # Get match counts per flight
    match_counts = {}
    if flight_ids:
        mc_q = await db.execute(
            select(FlightPhotoMatch.flight_id, func.count(FlightPhotoMatch.id))
            .where(FlightPhotoMatch.flight_id.in_(flight_ids))
            .group_by(FlightPhotoMatch.flight_id)
        )
        match_counts = dict(mc_q.all())

    # Build status info per flight
    def _build_status(flight):
        jobs = scrape_by_reg.get(flight.registration, [])
        mc = match_counts.get(flight.id, 0)
        if not jobs and not flight.registration:
            return {"type": "no_reg"}

        statuses = {j.status for j in jobs}
        any_running = "running" in statuses
        any_pending = "pending" in statuses
        any_failed = "failed" in statuses
        any_blocked = "blocked" in statuses
        any_completed = "completed" in statuses
        done_statuses = {"completed", "blocked"}
        all_done = bool(jobs) and statuses <= done_statuses

        last_times = [j.last_scraped_at for j in jobs if j.last_scraped_at]
        last_at = max(last_times) if last_times else None
        last_rel = ""
        if last_at:
            now = datetime.now(timezone.utc)
            delta = now - last_at
            if delta.total_seconds() < 60:
                last_rel = "just now"
            elif delta.total_seconds() < 3600:
                last_rel = f"{int(delta.total_seconds() // 60)}m ago"
            elif delta.total_seconds() < 86400:
                last_rel = f"{int(delta.total_seconds() // 3600)}h ago"
            elif delta.days < 7:
                last_rel = f"{delta.days}d ago"
            else:
                last_rel = f"{delta.days // 7}w ago"

        return {
            "type": "status",
            "match_count": mc,
            "any_running": any_running,
            "any_pending": any_pending,
            "any_failed": any_failed,
            "any_blocked": any_blocked,
            "any_completed": any_completed,
            "all_done": all_done,
            "last_scraped_relative": last_rel,
            "blocked_message": next((j.error_message for j in jobs if j.status == "blocked" and j.error_message), ""),
            "error_message": next((j.error_message for j in jobs if j.error_message), ""),
            "is_active": any_running or any_pending,
        }

    flight_statuses = {f.id: _build_status(f) for f in flights}

    return templates.TemplateResponse(
        "flights.html",
        {
            "request": request,
            "flights": flights,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "flight_statuses": flight_statuses,
        },
    )


@router.get("/flights/{flight_id}", response_class=HTMLResponse)
async def flight_detail(
    request: Request, flight_id: int, db: AsyncSession = Depends(get_db)
):
    flight_q = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = flight_q.scalar_one_or_none()
    if not flight:
        return HTMLResponse("Flight not found", status_code=404)

    matches_q = await db.execute(
        select(FlightPhotoMatch)
        .where(FlightPhotoMatch.flight_id == flight_id)
        .options(selectinload(FlightPhotoMatch.photo), selectinload(FlightPhotoMatch.decision))
        .order_by(FlightPhotoMatch.match_score.desc())
    )
    matches = matches_q.scalars().all()

    # Get scrape status for this registration
    scrape_q = await db.execute(
        select(ScrapeJob).where(ScrapeJob.registration == flight.registration)
    )
    scrape_jobs = scrape_q.scalars().all() if flight.registration else []

    # Count actual flight-photo matches per source
    match_counts_q = await db.execute(
        select(CandidatePhoto.source, func.count(FlightPhotoMatch.id))
        .join(CandidatePhoto, FlightPhotoMatch.photo_id == CandidatePhoto.id)
        .where(FlightPhotoMatch.flight_id == flight_id)
        .group_by(CandidatePhoto.source)
    )
    match_counts_by_source = dict(match_counts_q.all())

    return templates.TemplateResponse(
        "flight_detail.html",
        {
            "request": request,
            "flight": flight,
            "matches": matches,
            "scrape_jobs": scrape_jobs,
            "match_counts_by_source": match_counts_by_source,
        },
    )


@router.get("/flights/{flight_id}/scrape-status", response_class=HTMLResponse)
async def flight_scrape_status(
    request: Request, flight_id: int, db: AsyncSession = Depends(get_db)
):
    """HTMX endpoint for polling scrape status."""
    flight_q = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = flight_q.scalar_one_or_none()
    if not flight:
        return HTMLResponse("")

    if not flight.registration:
        return templates.TemplateResponse(
            "partials/scrape_status.html",
            {
                "request": request,
                "scrape_jobs": [],
                "match_count": 0,
                "flight_id": flight_id,
                "any_running": False,
                "any_pending": False,
                "any_failed": False,
                "any_blocked": False,
                "any_completed": False,
                "all_done": False,
                "last_scraped": "",
                "last_scraped_relative": "",
                "error_message": "",
                "blocked_message": "",
            },
        )

    scrape_q = await db.execute(
        select(ScrapeJob).where(ScrapeJob.registration == flight.registration)
    )
    scrape_jobs = scrape_q.scalars().all()

    match_count_q = await db.execute(
        select(func.count(FlightPhotoMatch.id)).where(
            FlightPhotoMatch.flight_id == flight_id
        )
    )
    match_count = match_count_q.scalar() or 0

    statuses = {j.status for j in scrape_jobs}
    last_scraped_times = [j.last_scraped_at for j in scrape_jobs if j.last_scraped_at]
    last_scraped_at = max(last_scraped_times) if last_scraped_times else None

    last_scraped = ""
    last_scraped_relative = ""
    if last_scraped_at:
        last_scraped = last_scraped_at.strftime("%Y-%m-%d %H:%M")
        now = datetime.now(timezone.utc)
        delta = now - last_scraped_at
        if delta.total_seconds() < 60:
            last_scraped_relative = "just now"
        elif delta.total_seconds() < 3600:
            mins = int(delta.total_seconds() // 60)
            last_scraped_relative = f"{mins}m ago"
        elif delta.total_seconds() < 86400:
            hours = int(delta.total_seconds() // 3600)
            last_scraped_relative = f"{hours}h ago"
        elif delta.days < 7:
            last_scraped_relative = f"{delta.days}d ago"
        else:
            weeks = delta.days // 7
            last_scraped_relative = f"{weeks}w ago"

    # Finished states: completed or blocked
    done_statuses = {"completed", "blocked"}
    error_msgs = [j.error_message for j in scrape_jobs if j.error_message]
    blocked_msgs = [j.error_message for j in scrape_jobs if j.status == "blocked" and j.error_message]

    return templates.TemplateResponse(
        "partials/scrape_status.html",
        {
            "request": request,
            "scrape_jobs": scrape_jobs,
            "match_count": match_count,
            "flight_id": flight_id,
            "any_running": "running" in statuses,
            "any_pending": "pending" in statuses,
            "any_failed": "failed" in statuses,
            "any_blocked": "blocked" in statuses,
            "any_completed": "completed" in statuses,
            "all_done": statuses <= done_statuses,
            "last_scraped": last_scraped,
            "last_scraped_relative": last_scraped_relative,
            "error_message": error_msgs[0] if error_msgs else "",
            "blocked_message": blocked_msgs[0] if blocked_msgs else "",
        },
    )


@router.post("/flights/{flight_id}/rescan", response_class=HTMLResponse)
async def rescan_flight(
    request: Request, flight_id: int, db: AsyncSession = Depends(get_db)
):
    """Manually trigger a rescan for a flight's registration."""
    flight_q = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = flight_q.scalar_one_or_none()
    if not flight or not flight.registration:
        return HTMLResponse('<span class="badge badge-failed">No registration</span>')

    result = await db.execute(
        select(ScrapeJob).where(ScrapeJob.registration == flight.registration)
    )
    jobs = result.scalars().all()

    now = datetime.now(timezone.utc)
    for job in jobs:
        job.status = "pending"
        job.next_scrape_after = now

    await db.commit()

    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        for job in jobs:
            await pool.enqueue_job("process_scrape_job", job.id)
        await pool.aclose()
    except Exception as e:
        logger.warning(f"Failed to enqueue rescan: {e}")

    return HTMLResponse('<span class="badge badge-running">scanning...</span>')
