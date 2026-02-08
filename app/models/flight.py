import uuid
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Flight(Base):
    __tablename__ = "flights"
    __table_args__ = (
        UniqueConstraint("import_batch_id", "row_index", name="uq_flight_batch_row"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    row_index: Mapped[int] = mapped_column()

    date: Mapped[date] = mapped_column(Date)
    flight_number: Mapped[str | None] = mapped_column(String(20))
    departure_city: Mapped[str | None] = mapped_column(String(100))
    departure_airport_name: Mapped[str | None] = mapped_column(String(200))
    departure_airport_iata: Mapped[str | None] = mapped_column(String(4), index=True)
    departure_airport_icao: Mapped[str | None] = mapped_column(String(4))
    arrival_city: Mapped[str | None] = mapped_column(String(100))
    arrival_airport_name: Mapped[str | None] = mapped_column(String(200))
    arrival_airport_iata: Mapped[str | None] = mapped_column(String(4), index=True)
    arrival_airport_icao: Mapped[str | None] = mapped_column(String(4))
    dep_time: Mapped[time | None] = mapped_column(Time)
    arr_time: Mapped[time | None] = mapped_column(Time)
    duration: Mapped[time | None] = mapped_column(Time)
    departure_datetime_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrival_datetime_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrival_date: Mapped[date | None] = mapped_column(Date, index=True)
    airline: Mapped[str | None] = mapped_column(String(100))
    aircraft: Mapped[str | None] = mapped_column(String(100))
    registration: Mapped[str | None] = mapped_column(String(20), index=True)
    seat_number: Mapped[str | None] = mapped_column(String(10))
    seat_type: Mapped[str | None] = mapped_column(String(20))
    flight_class: Mapped[str | None] = mapped_column(String(20))
    flight_reason: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(500))

    photo_matches: Mapped[list["FlightPhotoMatch"]] = relationship(
        "FlightPhotoMatch", back_populates="flight", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Flight {self.flight_number} {self.departure_airport_iata}->{self.arrival_airport_iata} {self.date}>"


from app.models.photo import FlightPhotoMatch  # noqa: E402, F401
