import logging
import time
from datetime import datetime, timedelta, timezone

from arq import create_pool, cron
from arq.connections import RedisSettings
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.flight import Flight
from app.models.photo import CandidatePhoto
from app.models.scrape_job import ScrapeJob, ScrapeRun
from app.scrapers.airlinersnet import AirlinersNetScraper
from app.scrapers.airplane_pictures import AirplanePicturesScraper
from app.scrapers.jetphotos import JetPhotosScraper
from app.scrapers.planespotters import PlanespottersScraper
from app.services.photo_matcher import match_photos_for_registration

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCRAPERS = {
    "jetphotos": JetPhotosScraper,
    "airlinersnet": AirlinersNetScraper,
    "planespotters": PlanespottersScraper,
    "airplane_pictures": AirplanePicturesScraper,
}


async def _enqueue_next_job(ctx: dict) -> None:
    """Self-schedule: after finishing a job, fill up to max_jobs."""
    redis = ctx.get("redis")

    if redis and await redis.get("ts:paused"):
        return

    # Read concurrency limit (default 3)
    max_jobs = 3
    if redis:
        max_jobs_raw = await redis.get("ts:max_jobs")
        if max_jobs_raw:
            max_jobs = int(max_jobs_raw)

    # Configurable delay between jobs (default 5s)
    delay = 5
    if redis:
        delay_raw = await redis.get("ts:job_delay")
        if delay_raw:
            delay = int(delay_raw)

    async with async_session() as db:
        # Fill up to max_jobs
        running_q = await db.execute(
            select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "running")
        )
        running_count = running_q.scalar() or 0
        slots = max_jobs - running_count
        if slots <= 0:
            logger.debug(f"No slots available ({running_count}/{max_jobs} running)")
            return

        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ScrapeJob).where(
                ScrapeJob.status.in_(["pending", "completed"]),
                ScrapeJob.next_scrape_after <= now,
            ).order_by(ScrapeJob.priority.desc()).limit(slots)
        )
        jobs = result.scalars().all()
        if not jobs:
            return

        enqueued = 0
        try:
            pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            for job in jobs:
                result = await pool.enqueue_job(
                    "process_scrape_job", job.id,
                    _defer_by=timedelta(seconds=delay),
                )
                if result:
                    enqueued += 1
            await pool.close()
            logger.info(
                f"Self-scheduled {enqueued}/{len(jobs)} jobs "
                f"(running={running_count}, max={max_jobs}, delay={delay}s)"
            )
        except Exception as e:
            logger.warning(f"Failed to self-schedule next jobs: {e}")


async def process_scrape_job(ctx: dict, job_id: int) -> dict:
    """Process a single scrape job."""
    async with async_session() as db:
        result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            await _enqueue_next_job(ctx)
            return {"error": f"Job {job_id} not found"}

        # Skip if already running (duplicate enqueue)
        if job.status == "running":
            logger.debug(f"Job {job_id} already running, skipping duplicate")
            await _enqueue_next_job(ctx)
            return {"skipped": True}

        # Check pause flag before doing any work
        redis = ctx.get("redis")
        if redis and await redis.get("ts:paused"):
            job.status = "pending"
            job.next_scrape_after = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"Job {job_id} paused before start, reset to pending")
            return {"paused": True}

        job.status = "running"
        await db.commit()

        scraper_cls = SCRAPERS.get(job.source)
        if not scraper_cls:
            job.status = "failed"
            job.error_message = f"Unknown source: {job.source}"
            await db.commit()
            await _enqueue_next_job(ctx)
            return {"error": job.error_message}

        run = ScrapeRun(
            job_id=job.id,
            source=job.source,
            registration=job.registration,
            status="running",
        )
        db.add(run)
        await db.commit()

        start = time.time()
        try:
            # Check pause flag again right before the expensive scrape
            if redis and await redis.get("ts:paused"):
                job.status = "pending"
                job.next_scrape_after = datetime.now(timezone.utc)
                run.status = "failed"
                run.error_message = "Paused"
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()
                logger.info(f"Job {job_id} paused before scrape, reset to pending")
                return {"paused": True}

            # Build set of plausible (date, airport) pairs from flights
            flights_q = await db.execute(
                select(Flight).where(Flight.registration == job.registration)
            )
            flights = flights_q.scalars().all()

            plausible_dates = set()  # dates within ±1 day of a flight
            plausible_airports = set()  # airports on any flight
            for f in flights:
                if f.date:
                    for offset in (-1, 0, 1):
                        plausible_dates.add(f.date + timedelta(days=offset))
                if f.arrival_date:
                    for offset in (-1, 0, 1):
                        plausible_dates.add(f.arrival_date + timedelta(days=offset))
                for code in (
                    f.departure_airport_iata, f.arrival_airport_iata,
                    f.departure_airport_icao, f.arrival_airport_icao,
                ):
                    if code:
                        plausible_airports.add(code.upper())

            scraper = scraper_cls()
            scraped = await scraper.scrape_registration(
                job.registration, airport_codes=plausible_airports
            )

            photos_found = 0
            photos_skipped = 0
            for sp in scraped:
                # Only save photos that could match a flight
                date_ok = sp.photo_date in plausible_dates if sp.photo_date else False
                airport_ok = (
                    sp.airport_code and sp.airport_code.upper() in plausible_airports
                )
                if not (date_ok and airport_ok):
                    photos_skipped += 1
                    continue

                existing = await db.execute(
                    select(CandidatePhoto).where(
                        CandidatePhoto.source == sp.source,
                        CandidatePhoto.source_photo_id == sp.source_photo_id,
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                photo = CandidatePhoto(
                    source=sp.source,
                    source_photo_id=sp.source_photo_id,
                    source_url=sp.source_url,
                    thumbnail_url=sp.thumbnail_url,
                    full_image_url=sp.full_image_url,
                    registration=sp.registration,
                    airport_code=sp.airport_code,
                    photo_date=sp.photo_date,
                    photographer=sp.photographer,
                )
                db.add(photo)
                photos_found += 1

            await db.commit()
            if photos_skipped:
                logger.info(
                    f"Filtered {photos_skipped} non-matching photos for {job.registration}"
                )

            # Run photo matching
            await match_photos_for_registration(db, job.registration)

            duration = time.time() - start
            run.status = "success"
            run.photos_found = photos_found
            run.duration_seconds = duration
            run.finished_at = datetime.now(timezone.utc)

            job.status = "completed"
            job.photos_found = (job.photos_found or 0) + photos_found
            job.last_scraped_at = datetime.now(timezone.utc)

            # Read rescan interval from Redis (default 168h = 7 days, 0 = never)
            rescan_hours = 168
            if redis:
                rescan_raw = await redis.get("ts:rescan_interval")
                if rescan_raw is not None:
                    rescan_hours = int(rescan_raw)
            if rescan_hours > 0:
                job.next_scrape_after = job.last_scraped_at + timedelta(hours=rescan_hours)
            else:
                job.next_scrape_after = None

            job.error_message = None

            await db.commit()

            logger.info(
                f"Scraped {job.registration} from {job.source}: "
                f"{photos_found} new photos in {duration:.1f}s"
            )
            await _enqueue_next_job(ctx)
            return {"photos_found": photos_found, "duration": duration}

        except PermissionError as e:
            # Permanent block (e.g. Cloudflare) — don't retry
            duration = time.time() - start
            run.status = "failed"
            run.error_message = str(e)
            run.duration_seconds = duration
            run.finished_at = datetime.now(timezone.utc)

            job.status = "blocked"
            job.error_message = str(e)
            job.next_scrape_after = None  # Don't retry

            await db.commit()
            logger.warning(f"Scrape blocked for {job.registration}/{job.source}: {e}")
            await _enqueue_next_job(ctx)
            return {"error": str(e), "blocked": True}

        except Exception as e:
            duration = time.time() - start
            run.status = "failed"
            run.error_message = str(e)
            run.duration_seconds = duration
            run.finished_at = datetime.now(timezone.utc)

            job.status = "failed"
            job.error_message = str(e)
            job.next_scrape_after = datetime.now(timezone.utc) + timedelta(hours=1)

            await db.commit()
            logger.error(f"Scrape failed for {job.registration}/{job.source}: {e}")
            await _enqueue_next_job(ctx)
            return {"error": str(e)}


async def check_pending_jobs(ctx: dict) -> None:
    """Fallback sweeper: pick up stalled jobs every 5 minutes.

    Primary dispatch is self-scheduling via _enqueue_next_job().
    This cron catches jobs that fell through (e.g. worker crash)
    and reaps jobs stuck in "running" for over 10 minutes.
    """
    redis = ctx.get("redis")

    # Reap stale running jobs (stuck for > 10 minutes)
    async with async_session() as db:
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(minutes=10)

        result = await db.execute(
            select(ScrapeJob).where(ScrapeJob.status == "running")
        )
        running_jobs = result.scalars().all()

        reaped = 0
        for job in running_jobs:
            # Check the most recent ScrapeRun for this job
            run_q = await db.execute(
                select(ScrapeRun).where(
                    ScrapeRun.job_id == job.id,
                    ScrapeRun.status == "running",
                ).order_by(ScrapeRun.started_at.desc()).limit(1)
            )
            run = run_q.scalar_one_or_none()

            if run and run.started_at < stale_cutoff:
                job.status = "failed"
                job.error_message = "Timed out after 10 minutes"
                job.next_scrape_after = now + timedelta(hours=1)
                run.status = "failed"
                run.error_message = "Timed out after 10 minutes"
                run.finished_at = now
                if run.started_at:
                    run.duration_seconds = (now - run.started_at).total_seconds()
                reaped += 1
            elif not run:
                # No run record — job was marked running but never started
                job.status = "failed"
                job.error_message = "Stuck in running with no active scrape run"
                job.next_scrape_after = now + timedelta(hours=1)
                reaped += 1

        if reaped:
            await db.commit()
            logger.warning(f"Sweeper: reaped {reaped} stale running jobs")

    if redis and await redis.get("ts:paused"):
        logger.debug("Queue is paused, skipping sweeper")
        return

    # Read max_jobs from Redis (default 3)
    max_jobs = 3
    if redis:
        max_jobs_raw = await redis.get("ts:max_jobs")
        if max_jobs_raw:
            max_jobs = int(max_jobs_raw)

    async with async_session() as db:
        # Only fill up to max_jobs minus currently running
        running_q = await db.execute(
            select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "running")
        )
        running_count = running_q.scalar() or 0
        slots = max_jobs - running_count

        logger.info(f"Sweeper: running={running_count}, max={max_jobs}, slots={slots}")

        if slots <= 0:
            return

        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ScrapeJob).where(
                ScrapeJob.status.in_(["pending", "completed"]),
                ScrapeJob.next_scrape_after <= now,
            ).order_by(ScrapeJob.priority.desc()).limit(slots)
        )
        jobs = result.scalars().all()

        if not jobs:
            logger.info("Sweeper: no eligible jobs ready")
            return

        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        enqueued = 0
        for job in jobs:
            r = await pool.enqueue_job("process_scrape_job", job.id)
            if r:
                enqueued += 1
        logger.info(f"Sweeper enqueued {enqueued}/{len(jobs)} jobs")

        await pool.close()


async def startup(ctx: dict) -> None:
    """Reset stale 'running' jobs and seed the queue on startup.

    Retries on DB errors (e.g. tables not created yet) to handle the case
    where the worker starts before the web container runs migrations.
    """
    import asyncio

    redis = ctx.get("redis")

    # Wait for database tables to be ready (migrations run in web container)
    for attempt in range(1, 21):
        try:
            async with async_session() as db:
                # Clean up stale running jobs from previous crash/restart
                result = await db.execute(
                    select(ScrapeJob).where(ScrapeJob.status == "running")
                )
                stale_jobs = result.scalars().all()
                if stale_jobs:
                    now = datetime.now(timezone.utc)
                    for job in stale_jobs:
                        job.status = "pending"
                        job.next_scrape_after = now
                    await db.commit()
                    logger.info(f"Reset {len(stale_jobs)} stale running jobs to pending")
            break
        except Exception as e:
            if attempt < 20:
                logger.info(f"Waiting for database tables (attempt {attempt}/20): {e}")
                await asyncio.sleep(3)
            else:
                logger.warning(f"Database not ready after 20 attempts, starting anyway: {e}")

    # Seed the queue if not paused
    try:
        if redis and not await redis.get("ts:paused"):
            max_jobs = 3
            max_jobs_raw = await redis.get("ts:max_jobs")
            if max_jobs_raw:
                max_jobs = int(max_jobs_raw)

            async with async_session() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(ScrapeJob).where(
                        ScrapeJob.status.in_(["pending", "completed"]),
                        ScrapeJob.next_scrape_after <= now,
                    ).order_by(ScrapeJob.priority.desc()).limit(max_jobs)
                )
                jobs = result.scalars().all()

            if jobs:
                pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
                for job in jobs:
                    await pool.enqueue_job("process_scrape_job", job.id)
                await pool.close()
                logger.info(f"Seeded {len(jobs)} jobs on startup")
    except Exception as e:
        logger.info(f"No jobs to seed on startup: {e}")

    logger.info("Scrape worker started")


async def shutdown(ctx: dict) -> None:
    logger.info("Scrape worker stopped")


class WorkerSettings:
    functions = [process_scrape_job]
    cron_jobs = [cron(check_pending_jobs, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}, second={0})]  # every 5 min fallback
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 10
    job_timeout = 300


if __name__ == "__main__":
    import arq.cli

    logging.basicConfig(level=logging.INFO)
    arq.cli.main()
