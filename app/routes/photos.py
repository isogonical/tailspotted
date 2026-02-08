from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.flight import Flight
from app.models.photo import CandidatePhoto, FlightPhotoMatch, UserDecision
from app.services.timezone_resolver import to_iata

_SOURCE_NAMES = {
    "airlinersnet": "Airliners.net",
    "jetphotos": "JetPhotos",
    "planespotters": "Planespotters.net",
    "airplane_pictures": "Airplane-Pictures.net",
}

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["to_iata"] = to_iata
templates.env.filters["source_display"] = lambda s: _SOURCE_NAMES.get(s, s)


def _pending_query():
    """Base query for undecided matches, ordered by score desc."""
    decided_subq = select(UserDecision.match_id).where(
        UserDecision.decision.in_(["approved", "rejected"])
    )
    return (
        select(FlightPhotoMatch)
        .where(FlightPhotoMatch.id.notin_(decided_subq))
        .order_by(FlightPhotoMatch.match_score.desc(), FlightPhotoMatch.id)
    )


async def _get_review_at_index(db: AsyncSession, index: int) -> tuple | None:
    """Get the match at a given position in the pending queue."""
    result = await db.execute(_pending_query().offset(index).limit(1))
    match = result.scalar_one_or_none()
    if not match:
        return None

    flight_q = await db.execute(select(Flight).where(Flight.id == match.flight_id))
    flight = flight_q.scalar_one_or_none()

    photo_q = await db.execute(
        select(CandidatePhoto).where(CandidatePhoto.id == match.photo_id)
    )
    photo = photo_q.scalar_one_or_none()

    return match, flight, photo


async def _pending_count(db: AsyncSession) -> int:
    decided_subq = select(UserDecision.match_id).where(
        UserDecision.decision.in_(["approved", "rejected"])
    )
    result = await db.execute(
        select(func.count(FlightPhotoMatch.id)).where(
            FlightPhotoMatch.id.notin_(decided_subq)
        )
    )
    return result.scalar() or 0


@router.get("/review/pending-count", response_class=HTMLResponse)
async def pending_count_fragment(db: AsyncSession = Depends(get_db)):
    """Returns just the '(N pending)' text for HTMX swap."""
    total = await _pending_count(db)
    return HTMLResponse(f"({total} pending)")


async def _find_index_for_match(db: AsyncSession, match_id: int) -> int | None:
    """Find the position of a specific match in the pending queue."""
    # Get all pending match IDs in order
    result = await db.execute(_pending_query().with_only_columns(FlightPhotoMatch.id))
    ids = [row[0] for row in result.all()]
    try:
        return ids.index(match_id)
    except ValueError:
        return None


@router.get("/review", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    index: int = 0,
    match_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    total = await _pending_count(db)

    if total == 0:
        return templates.TemplateResponse(
            "photo_review.html",
            {"request": request, "match": None, "pending_count": 0},
        )

    # Deep link: find index for a specific match
    if match_id is not None:
        found_index = await _find_index_for_match(db, match_id)
        if found_index is not None:
            index = found_index

    # Clamp index
    if index < 0:
        index = 0
    if index >= total:
        index = total - 1

    item = await _get_review_at_index(db, index)

    if not item:
        return templates.TemplateResponse(
            "photo_review.html",
            {"request": request, "match": None, "pending_count": 0},
        )

    match, flight, photo = item

    return templates.TemplateResponse(
        "photo_review.html",
        {
            "request": request,
            "match": match,
            "flight": flight,
            "photo": photo,
            "pending_count": total,
            "index": index,
        },
    )


@router.get("/review/card", response_class=HTMLResponse)
async def review_card(
    request: Request, index: int = 0, db: AsyncSession = Depends(get_db)
):
    """HTMX endpoint for prev/next navigation — returns just the card partial."""
    total = await _pending_count(db)

    if total == 0:
        return templates.TemplateResponse(
            "partials/photo_card.html",
            {"request": request, "match": None, "pending_count": 0},
        )

    if index < 0:
        index = 0
    if index >= total:
        index = total - 1

    item = await _get_review_at_index(db, index)

    if not item:
        return templates.TemplateResponse(
            "partials/photo_card.html",
            {"request": request, "match": None, "pending_count": 0},
        )

    match, flight, photo = item

    return templates.TemplateResponse(
        "partials/photo_card.html",
        {
            "request": request,
            "match": match,
            "flight": flight,
            "photo": photo,
            "pending_count": total,
            "index": index,
        },
    )


@router.post("/review/{match_id}/{decision}", response_class=HTMLResponse)
async def review_decision(
    request: Request,
    match_id: int,
    decision: str,
    index: int = 0,
    comment: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    if decision not in ("approved", "rejected"):
        return HTMLResponse("Invalid decision", status_code=400)

    clean_comment = comment.strip() or None

    existing_q = await db.execute(
        select(UserDecision).where(UserDecision.match_id == match_id)
    )
    existing = existing_q.scalar_one_or_none()
    if existing:
        existing.decision = decision
        existing.comment = clean_comment
        existing.decided_at = datetime.now(timezone.utc)
    else:
        db.add(UserDecision(
            match_id=match_id,
            decision=decision,
            comment=clean_comment,
            decided_at=datetime.now(timezone.utc),
        ))
    await db.commit()

    # After removing an item, the same index now points to the next item.
    # If we were at the end, step back.
    total = await _pending_count(db)

    if total == 0:
        resp = templates.TemplateResponse(
            "partials/photo_card.html",
            {"request": request, "match": None, "pending_count": 0},
        )
        resp.headers["HX-Trigger"] = "reviewCountChanged"
        return resp

    if index >= total:
        index = total - 1

    item = await _get_review_at_index(db, index)

    if not item:
        resp = templates.TemplateResponse(
            "partials/photo_card.html",
            {"request": request, "match": None, "pending_count": 0},
        )
        resp.headers["HX-Trigger"] = "reviewCountChanged"
        return resp

    match, flight, photo = item

    resp = templates.TemplateResponse(
        "partials/photo_card.html",
        {
            "request": request,
            "match": match,
            "flight": flight,
            "photo": photo,
            "pending_count": total,
            "index": index,
        },
    )
    resp.headers["HX-Trigger"] = "reviewCountChanged"
    return resp
