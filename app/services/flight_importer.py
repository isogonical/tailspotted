"""Shared flight import logic — dedup, persist, create scrape jobs, seed queue."""

import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.flight import Flight
from app.models.scrape_job import ScrapeJob
from app.services.scrape_orchestrator import create_scrape_jobs_for_batch

logger = logging.getLogger(__name__)


async def import_flights(
    flights: list[Flight], batch_id, db: AsyncSession
) -> dict:
    """Dedup flights, insert new ones, create scrape jobs, and seed the queue.

    Returns dict with import stats: flights_imported, flights_skipped,
    registrations, jobs_created, batch_id.
    """
    new_flights = []
    skipped = 0
    for flight in flights:
        conditions = [
            Flight.date == flight.date,
            Flight.departure_airport_iata == flight.departure_airport_iata,
            Flight.arrival_airport_iata == flight.arrival_airport_iata,
        ]
        if flight.flight_number:
            conditions.append(Flight.flight_number == flight.flight_number)
        else:
            conditions.append(Flight.flight_number.is_(None))
        if flight.dep_time:
            conditions.append(Flight.dep_time == flight.dep_time)

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

    # Seed the queue
    try:
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

    return {
        "flights_imported": len(new_flights),
        "flights_skipped": skipped,
        "registrations": len(regs),
        "jobs_created": jobs_created,
        "batch_id": str(batch_id),
    }
