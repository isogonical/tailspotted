from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    registration: Mapped[str] = mapped_column(String(20), index=True)
    source: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending / running / completed / failed
    priority: Mapped[int] = mapped_column(Integer, default=0)
    photos_found: Mapped[int] = mapped_column(Integer, default=0)
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scrape_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(30))
    registration: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))  # success / failed
    photos_found: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column()
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
