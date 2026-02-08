from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.flight import Flight  # noqa: E402, F401
from app.models.photo import CandidatePhoto, FlightPhotoMatch, UserDecision  # noqa: E402, F401
from app.models.scrape_job import ScrapeJob, ScrapeRun  # noqa: E402, F401
