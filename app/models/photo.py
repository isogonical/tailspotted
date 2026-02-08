from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class CandidatePhoto(Base):
    __tablename__ = "candidate_photos"
    __table_args__ = (
        UniqueConstraint("source", "source_photo_id", name="uq_source_photo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(30))
    source_photo_id: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str] = mapped_column(String(500))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    full_image_url: Mapped[str | None] = mapped_column(String(500))
    registration: Mapped[str] = mapped_column(String(20), index=True)
    airport_code: Mapped[str | None] = mapped_column(String(4))
    photo_date: Mapped[date | None] = mapped_column(Date, index=True)
    photographer: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    matches: Mapped[list["FlightPhotoMatch"]] = relationship(
        "FlightPhotoMatch", back_populates="photo", lazy="selectin"
    )


class FlightPhotoMatch(Base):
    __tablename__ = "flight_photo_matches"
    __table_args__ = (
        UniqueConstraint("flight_id", "photo_id", name="uq_flight_photo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    flight_id: Mapped[int] = mapped_column(
        ForeignKey("flights.id", ondelete="CASCADE"), index=True
    )
    photo_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_photos.id", ondelete="CASCADE"), index=True
    )
    match_score: Mapped[int] = mapped_column(Integer, index=True)
    match_reasons: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    flight: Mapped["Flight"] = relationship("Flight", back_populates="photo_matches")
    photo: Mapped["CandidatePhoto"] = relationship(
        "CandidatePhoto", back_populates="matches"
    )
    decision: Mapped["UserDecision | None"] = relationship(
        "UserDecision", back_populates="match", uselist=False, lazy="selectin"
    )


class UserDecision(Base):
    __tablename__ = "user_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("flight_photo_matches.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(20))  # approved / rejected
    comment: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    match: Mapped["FlightPhotoMatch"] = relationship(
        "FlightPhotoMatch", back_populates="decision"
    )


from app.models.flight import Flight  # noqa: E402, F401
