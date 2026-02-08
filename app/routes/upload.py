import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.flight import Flight
from app.models.photo import CandidatePhoto, FlightPhotoMatch, UserDecision
from app.models.scrape_job import ScrapeJob, ScrapeRun
from app.services.flight_importer import import_flights
from app.services.parsers import parse_flight_file

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_REDIS_KEY_URL = "ts:airtrail_url"
_REDIS_KEY_API_KEY = "ts:airtrail_api_key"
_REDIS_KEY_SCHEDULE = "ts:airtrail_schedule"
_REDIS_KEY_LAST_SYNC = "ts:airtrail_last_sync"


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@router.post("/import", response_class=HTMLResponse)
async def import_file(request: Request, file: UploadFile, db: AsyncSession = Depends(get_db)):
    content = await file.read()

    try:
        flights, batch_id, format_info = parse_flight_file(content, file.filename or "")
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return templates.TemplateResponse(
            "partials/upload_result.html",
            {"request": request, "error": f"Failed to parse file: {e}"},
        )

    stats = await import_flights(flights, batch_id, db)

    return templates.TemplateResponse(
        "partials/upload_result.html",
        {
            "request": request,
            **stats,
            "format_name": format_info.name,
            "format_beta": format_info.beta,
        },
    )


# ===== AirTrail sync endpoints =====

async def _read_airtrail_config() -> dict:
    """Read all AirTrail keys from Redis."""
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    url = await r.get(_REDIS_KEY_URL)
    api_key = await r.get(_REDIS_KEY_API_KEY)
    schedule = await r.get(_REDIS_KEY_SCHEDULE) or "manual"
    last_sync = await r.get(_REDIS_KEY_LAST_SYNC)
    await r.aclose()
    return {
        "url": url,
        "api_key": api_key,
        "schedule": schedule,
        "last_sync": last_sync,
    }


def _airtrail_ctx(request: Request, cfg: dict, **extra) -> dict:
    """Build template context for the airtrail_status partial."""
    return {
        "request": request,
        "configured": bool(cfg["url"] and cfg["api_key"]),
        "airtrail_url": cfg["url"] or "",
        "sync_schedule": cfg["schedule"],
        "last_sync": cfg["last_sync"],
        **extra,
    }


@router.get("/airtrail/status", response_class=HTMLResponse)
async def airtrail_status(request: Request):
    """Return HTMX partial showing current AirTrail connection state."""
    cfg = await _read_airtrail_config()
    return templates.TemplateResponse(
        "partials/airtrail_status.html",
        _airtrail_ctx(request, cfg),
    )


@router.post("/airtrail/save", response_class=HTMLResponse)
async def airtrail_save(
    request: Request,
    url: str = Form(...),
    api_key: str = Form(...),
):
    """Save AirTrail config to Redis after testing the connection."""
    from app.services.airtrail_sync import test_airtrail_connection

    url = url.strip().rstrip("/")
    api_key = api_key.strip()

    if not url or not api_key:
        cfg = await _read_airtrail_config()
        return templates.TemplateResponse(
            "partials/airtrail_status.html",
            _airtrail_ctx(request, cfg, editing=True, error="URL and API key are required"),
        )

    ok, message = await test_airtrail_connection(url, api_key)

    if not ok:
        cfg = await _read_airtrail_config()
        # Show edit form with the attempted URL pre-filled
        return templates.TemplateResponse(
            "partials/airtrail_status.html",
            {
                "request": request,
                "editing": True,
                "configured": False,
                "airtrail_url": url,
                "sync_schedule": cfg["schedule"],
                "last_sync": cfg["last_sync"],
                "error": message,
            },
        )

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await r.set(_REDIS_KEY_URL, url)
    await r.set(_REDIS_KEY_API_KEY, api_key)
    await r.aclose()

    cfg = await _read_airtrail_config()
    return templates.TemplateResponse(
        "partials/airtrail_status.html",
        _airtrail_ctx(request, cfg, connect_message=message),
    )


@router.post("/airtrail/sync", response_class=HTMLResponse)
async def airtrail_sync(request: Request, db: AsyncSession = Depends(get_db)):
    """Run a sync from AirTrail and return import results."""
    from app.services.airtrail_sync import sync_airtrail_flights

    cfg = await _read_airtrail_config()

    if not cfg["url"] or not cfg["api_key"]:
        return templates.TemplateResponse(
            "partials/airtrail_status.html",
            _airtrail_ctx(request, cfg, error="AirTrail is not configured"),
        )

    try:
        flights, batch_id, format_info = await sync_airtrail_flights(cfg["url"], cfg["api_key"])
    except Exception as e:
        logger.error(f"AirTrail sync error: {e}")
        return templates.TemplateResponse(
            "partials/airtrail_status.html",
            _airtrail_ctx(request, cfg, sync_error=f"Sync failed: {e}"),
        )

    stats = await import_flights(flights, batch_id, db)

    # Record last sync time
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await r.set(_REDIS_KEY_LAST_SYNC, now_str)
    await r.aclose()

    cfg = await _read_airtrail_config()
    return templates.TemplateResponse(
        "partials/airtrail_status.html",
        _airtrail_ctx(request, cfg, sync_result={
            **stats,
            "format_name": format_info.name,
            "format_beta": format_info.beta,
        }),
    )


@router.get("/airtrail/edit", response_class=HTMLResponse)
async def airtrail_edit(request: Request):
    """Return the edit-connection form pre-filled with the current URL."""
    cfg = await _read_airtrail_config()
    return templates.TemplateResponse(
        "partials/airtrail_status.html",
        _airtrail_ctx(request, cfg, editing=True),
    )


@router.post("/airtrail/schedule", response_class=HTMLResponse)
async def airtrail_schedule(request: Request, schedule: str = Form(...)):
    """Save the auto-sync schedule to Redis."""
    valid = {"manual", "1h", "6h", "12h", "24h"}
    if schedule not in valid:
        schedule = "manual"

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await r.set(_REDIS_KEY_SCHEDULE, schedule)
    await r.aclose()

    cfg = await _read_airtrail_config()
    return templates.TemplateResponse(
        "partials/airtrail_status.html",
        _airtrail_ctx(request, cfg),
    )


@router.post("/airtrail/disconnect", response_class=HTMLResponse)
async def airtrail_disconnect(request: Request):
    """Remove AirTrail config from Redis."""
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await r.delete(
        _REDIS_KEY_URL, _REDIS_KEY_API_KEY,
        _REDIS_KEY_SCHEDULE, _REDIS_KEY_LAST_SYNC,
    )
    await r.aclose()

    return templates.TemplateResponse(
        "partials/airtrail_status.html",
        {
            "request": request,
            "configured": False,
            "airtrail_url": "",
            "sync_schedule": "manual",
            "last_sync": None,
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
