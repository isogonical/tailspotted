from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flight import Flight
from app.models.scrape_job import ScrapeJob

SOURCES = ["jetphotos", "airlinersnet", "planespotters", "airplane_pictures"]


async def create_scrape_jobs_for_batch(
    db: AsyncSession, batch_id, flights: list[Flight]
) -> int:
    """Create scrape jobs for each unique registration in a batch of flights."""
    registrations = {f.registration for f in flights if f.registration}

    created = 0
    for reg in registrations:
        for source in SOURCES:
            existing = await db.execute(
                select(ScrapeJob).where(
                    ScrapeJob.registration == reg,
                    ScrapeJob.source == source,
                )
            )
            if existing.scalar_one_or_none():
                continue

            job = ScrapeJob(
                registration=reg,
                source=source,
                status="pending",
                priority=1,
                next_scrape_after=datetime.now(timezone.utc),
            )
            db.add(job)
            created += 1

    await db.commit()
    return created
