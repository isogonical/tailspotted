import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.routes import flights, library, photos, queue, upload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Tailspotted starting up")
    # Run alembic migrations on startup
    import subprocess

    try:
        subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Database migrations applied")
    except subprocess.CalledProcessError as e:
        logger.error(f"Migration failed: {e.stderr}")
    except FileNotFoundError:
        logger.warning("Alembic not found, skipping migrations")

    yield
    logger.info("Tailspotted shutting down")


app = FastAPI(title="Tailspotted", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(upload.router)
app.include_router(flights.router)
app.include_router(photos.router)
app.include_router(library.router)
app.include_router(queue.router)


@app.get("/")
async def root():
    return RedirectResponse(url="/flights")


@app.get("/review/count", response_class=HTMLResponse)
async def review_count():
    from app.database import async_session
    from app.models.photo import FlightPhotoMatch, UserDecision

    async with async_session() as db:
        decided_subq = select(UserDecision.match_id).where(
            UserDecision.decision.in_(["approved", "rejected"])
        )
        result = await db.execute(
            select(func.count(FlightPhotoMatch.id)).where(
                FlightPhotoMatch.id.notin_(decided_subq)
            )
        )
        count = result.scalar() or 0

    if count > 0:
        return HTMLResponse(f'<span class="nav-badge">{count}</span>')
    return HTMLResponse("")


@app.get("/health")
async def health():
    from app.database import async_session

    try:
        async with async_session() as db:
            await db.execute(select(func.now()))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
