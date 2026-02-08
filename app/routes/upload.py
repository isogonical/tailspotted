import logging
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.flight import Flight
from app.models.photo import CandidatePhoto, FlightPhotoMatch, UserDecision
from app.models.scrape_job import ScrapeJob, ScrapeRun
from app.services.csv_parser import parse_csv
from app.services.scrape_orchestrator import create_scrape_jobs_for_batch

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@router.post("/upload", response_class=HTMLResponse)
async def upload_csv(request: Request, file: UploadFile, db: AsyncSession = Depends(get_db)):
    content = await file.read()

    try:
        flights, batch_id = parse_csv(content)
    except Exception as e:
        logger.error(f"CSV parse error: {e}")
        return templates.TemplateResponse(
            "partials/upload_result.html",
            {"request": request, "error": f"Failed to parse CSV: {e}"},
        )

    # Dedup: only insert flights that don't already exist
    new_flights = []
    skipped = 0
    for flight in flights:
        # Build match conditions for natural key
        conditions = [
            Flight.date == flight.date,
            Flight.departure_airport_iata == flight.departure_airport_iata,
            Flight.arrival_airport_iata == flight.arrival_airport_iata,
        ]
        # Handle nullable fields — match NULL == NULL
        if flight.flight_number:
            conditions.append(Flight.flight_number == flight.flight_number)
        else:
            conditions.append(Flight.flight_number.is_(None))
        if flight.dep_time:
            conditions.append(Flight.dep_time == flight.dep_time)
        else:
            conditions.append(Flight.dep_time.is_(None))

        existing = await db.execute(
            select(Flight.id).where(and_(*conditions)).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        db.add(flight)
        new_flights.append(flight)

    await db.commit()

    jobs_created = await create_scrape_jobs_for_batch(db, batch_id, new_flights)

    # Seed the queue — only enqueue up to max_jobs to kick off self-scheduling
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        max_jobs_raw = await r.get("ts:max_jobs")
        max_jobs = int(max_jobs_raw) if max_jobs_raw else 3
        await r.aclose()

        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ScrapeJob).where(
                ScrapeJob.status == "pending",
                ScrapeJob.next_scrape_after <= now,
            ).order_by(ScrapeJob.priority.desc()).limit(max_jobs)
        )
        seed_jobs = result.scalars().all()
        if seed_jobs:
            pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            for job in seed_jobs:
                await pool.enqueue_job("process_scrape_job", job.id)
            await pool.close()
    except Exception as e:
        logger.warning(f"Failed to enqueue scrape jobs: {e}")

    regs = {f.registration for f in new_flights if f.registration}

    return templates.TemplateResponse(
        "partials/upload_result.html",
        {
            "request": request,
            "flights_imported": len(new_flights),
            "flights_skipped": skipped,
            "registrations": len(regs),
            "jobs_created": jobs_created,
            "batch_id": str(batch_id),
        },
    )


@router.post("/reset", response_class=HTMLResponse)
async def reset_all(request: Request, db: AsyncSession = Depends(get_db)):
    """Delete all flights, photos, matches, decisions, and scrape jobs."""
    # Delete in FK-safe order
    await db.execute(delete(UserDecision))
    await db.execute(delete(FlightPhotoMatch))
    await db.execute(delete(CandidatePhoto))
    await db.execute(delete(ScrapeRun))
    await db.execute(delete(ScrapeJob))
    await db.execute(delete(Flight))
    await db.commit()

    return templates.TemplateResponse(
        "partials/reset_result.html",
        {"request": request},
    )
