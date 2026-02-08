import logging
import math
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.scrape_job import ScrapeJob, ScrapeRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue")
templates = Jinja2Templates(directory="app/templates")


async def get_redis():
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


async def _kickstart_queue(db: AsyncSession, r: aioredis.Redis) -> None:
    """Enqueue pending jobs to seed the self-scheduling chain."""
    max_jobs_raw = await r.get("ts:max_jobs")
    max_jobs = int(max_jobs_raw) if max_jobs_raw else 3

    # Check how many are already running
    running_q = await db.execute(
        select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "running")
    )
    running_count = running_q.scalar() or 0
    slots = max_jobs - running_count
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
        return

    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        for job in jobs:
            await pool.enqueue_job("process_scrape_job", job.id)
            logger.info(f"Kickstart enqueued job {job.id}: {job.registration}/{job.source}")
        await pool.close()
    except Exception as e:
        logger.warning(f"Failed to kickstart queue: {e}")


async def _update_rescan_schedule(db: AsyncSession, hours: int) -> None:
    """Recompute next_scrape_after for all completed jobs based on new interval."""
    result = await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "completed")
    )
    jobs = result.scalars().all()
    for job in jobs:
        if hours > 0 and job.last_scraped_at:
            job.next_scrape_after = job.last_scraped_at + timedelta(hours=hours)
        else:
            job.next_scrape_after = None
    await db.commit()


def _relative_time(dt: datetime) -> str:
    """Format a future datetime as a relative time string like '5d 2h' or '3h 15m'."""
    now = datetime.now(timezone.utc)
    delta = dt - now
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "now"
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


async def get_queue_data(db: AsyncSession, r: aioredis.Redis) -> dict:
    """Gather all queue stats for the panel."""
    now = datetime.now(timezone.utc)

    # Active pending: status=pending with next_scrape_after in the past (ready now)
    pending_q = await db.execute(
        select(func.count(ScrapeJob.id)).where(
            ScrapeJob.status == "pending",
            ScrapeJob.next_scrape_after <= now,
        )
    )
    pending = pending_q.scalar() or 0

    # Running
    running_q = await db.execute(
        select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "running")
    )
    running = running_q.scalar() or 0

    # Failed
    failed_q = await db.execute(
        select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "failed")
    )
    failed = failed_q.scalar() or 0

    # Blocked
    blocked_q = await db.execute(
        select(func.count(ScrapeJob.id)).where(ScrapeJob.status == "blocked")
    )
    blocked = blocked_q.scalar() or 0

    # Completed: successfully scraped (not due for rescrape yet)
    completed_q = await db.execute(
        select(func.count(ScrapeJob.id)).where(
            ScrapeJob.status == "completed",
        )
    )
    completed = completed_q.scalar() or 0

    # Average duration from successful scrape runs
    avg_q = await db.execute(
        select(func.avg(ScrapeRun.duration_seconds)).where(
            ScrapeRun.status == "success",
            ScrapeRun.duration_seconds.isnot(None),
        )
    )
    avg_duration = avg_q.scalar() or 0

    # Upcoming rescans: completed jobs with a future next_scrape_after
    upcoming_q = await db.execute(
        select(func.count(ScrapeJob.id)).where(
            ScrapeJob.status == "completed",
            ScrapeJob.next_scrape_after.isnot(None),
            ScrapeJob.next_scrape_after > now,
        )
    )
    upcoming_count = upcoming_q.scalar() or 0

    next_rescan_str = None
    if upcoming_count > 0:
        next_q = await db.execute(
            select(func.min(ScrapeJob.next_scrape_after)).where(
                ScrapeJob.status == "completed",
                ScrapeJob.next_scrape_after.isnot(None),
                ScrapeJob.next_scrape_after > now,
            )
        )
        next_rescan_at = next_q.scalar()
        if next_rescan_at:
            next_rescan_str = _relative_time(next_rescan_at)

    # Read Redis control keys
    paused = bool(await r.get("ts:paused"))
    max_jobs_raw = await r.get("ts:max_jobs")
    max_jobs = int(max_jobs_raw) if max_jobs_raw else 3
    job_delay_raw = await r.get("ts:job_delay")
    job_delay = int(job_delay_raw) if job_delay_raw else 5
    rescan_interval_raw = await r.get("ts:rescan_interval")
    rescan_interval = int(rescan_interval_raw) if rescan_interval_raw is not None else 168

    # ETA: pending jobs each take avg_duration + delay between them
    if pending > 0 and avg_duration > 0:
        eta_seconds = pending * (avg_duration + job_delay)
        eta_minutes = max(1, math.ceil(eta_seconds / 60))
    else:
        eta_minutes = 0

    # Get failed job details
    failed_jobs_list = []
    if failed > 0:
        fj_q = await db.execute(
            select(ScrapeJob).where(ScrapeJob.status == "failed")
            .order_by(ScrapeJob.id)
        )
        failed_jobs_list = fj_q.scalars().all()

    return {
        "pending": pending,
        "running": running,
        "completed": completed,
        "failed": failed,
        "blocked": blocked,
        "paused": paused,
        "max_jobs": max_jobs,
        "job_delay": job_delay,
        "rescan_interval": rescan_interval,
        "upcoming_count": upcoming_count,
        "next_rescan_str": next_rescan_str,
        "eta_minutes": eta_minutes,
        "failed_jobs": failed_jobs_list,
    }


@router.get("/panel", response_class=HTMLResponse)
async def queue_panel(
    request: Request,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    data = await get_queue_data(db, r)
    return templates.TemplateResponse(
        "partials/queue_panel.html", {"request": request, **data}
    )


@router.get("/stats", response_class=HTMLResponse)
async def queue_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    data = await get_queue_data(db, r)
    return templates.TemplateResponse(
        "partials/queue_panel.html", {"request": request, **data}
    )


@router.post("/pause", response_class=HTMLResponse)
async def queue_pause(
    request: Request,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    await r.set("ts:paused", "1")
    data = await get_queue_data(db, r)
    return templates.TemplateResponse(
        "partials/queue_panel.html", {"request": request, **data}
    )


@router.post("/resume", response_class=HTMLResponse)
async def queue_resume(
    request: Request,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    await r.delete("ts:paused")
    await _kickstart_queue(db, r)
    data = await get_queue_data(db, r)
    return templates.TemplateResponse(
        "partials/queue_panel.html", {"request": request, **data}
    )


@router.post("/settings", response_class=HTMLResponse)
async def queue_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    form = await request.form()
    max_jobs = int(form.get("max_jobs", 3))
    job_delay = int(form.get("job_delay", 5))
    rescan_interval = int(form.get("rescan_interval", 168))

    # Clamp values
    max_jobs = max(1, min(10, max_jobs))
    job_delay = max(0, min(60, job_delay))
    if rescan_interval not in (0, 24, 72, 168, 336, 720):
        rescan_interval = 168

    await r.set("ts:max_jobs", str(max_jobs))
    await r.set("ts:job_delay", str(job_delay))
    await r.set("ts:rescan_interval", str(rescan_interval))

    # Retroactively update all completed jobs' next_scrape_after
    await _update_rescan_schedule(db, rescan_interval)

    data = await get_queue_data(db, r)
    return templates.TemplateResponse(
        "partials/queue_panel.html", {"request": request, **data}
    )


@router.post("/reprocess", response_class=HTMLResponse)
async def queue_reprocess(
    request: Request,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    now = datetime.now(timezone.utc)

    # 1. Pause the queue
    await r.set("ts:paused", "1")

    # 2. Reset running jobs → pending, fail their active ScrapeRun records
    result = await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "running")
    )
    running_jobs = result.scalars().all()
    for job in running_jobs:
        # Fail any active ScrapeRun records for this job
        runs_result = await db.execute(
            select(ScrapeRun).where(
                ScrapeRun.job_id == job.id,
                ScrapeRun.status == "running",
            )
        )
        for run in runs_result.scalars().all():
            run.status = "failed"
            run.error_message = "Queue reprocessed"
            run.finished_at = now

        job.status = "pending"
        job.next_scrape_after = now
        job.error_message = None

    # 3. Reset failed jobs → pending
    result = await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "failed")
    )
    failed_jobs = result.scalars().all()
    for job in failed_jobs:
        job.status = "pending"
        job.next_scrape_after = now
        job.error_message = None

    await db.commit()

    reset_count = len(running_jobs) + len(failed_jobs)
    logger.info(f"Queue reprocessed: reset {len(running_jobs)} running + {len(failed_jobs)} failed = {reset_count} jobs")

    # 4. Unpause and kickstart
    await r.delete("ts:paused")
    await _kickstart_queue(db, r)

    data = await get_queue_data(db, r)
    return templates.TemplateResponse(
        "partials/queue_panel.html", {"request": request, **data}
    )


@router.post("/retry-failed", response_class=HTMLResponse)
async def queue_retry_failed(
    request: Request,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    now = datetime.now(timezone.utc)

    # Reset failed jobs to pending
    result = await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "failed")
    )
    failed_jobs = result.scalars().all()

    for job in failed_jobs:
        job.status = "pending"
        job.next_scrape_after = now
        job.error_message = None

    await db.commit()

    # Seed the queue — only enqueue up to max_jobs, self-scheduling handles the rest
    if failed_jobs:
        try:
            max_jobs_raw = await r.get("ts:max_jobs")
            max_jobs = int(max_jobs_raw) if max_jobs_raw else 3
            pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            for job in failed_jobs[:max_jobs]:
                await pool.enqueue_job("process_scrape_job", job.id)
            await pool.close()
        except Exception as e:
            logger.warning(f"Failed to enqueue retry jobs: {e}")

    data = await get_queue_data(db, r)
    return templates.TemplateResponse(
        "partials/queue_panel.html", {"request": request, **data}
    )
