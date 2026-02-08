"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("import_batch_id", postgresql.UUID(as_uuid=True), index=True),
        sa.Column("row_index", sa.Integer()),
        sa.Column("date", sa.Date()),
        sa.Column("flight_number", sa.String(20)),
        sa.Column("departure_city", sa.String(100)),
        sa.Column("departure_airport_name", sa.String(200)),
        sa.Column("departure_airport_iata", sa.String(4), index=True),
        sa.Column("departure_airport_icao", sa.String(4)),
        sa.Column("arrival_city", sa.String(100)),
        sa.Column("arrival_airport_name", sa.String(200)),
        sa.Column("arrival_airport_iata", sa.String(4), index=True),
        sa.Column("arrival_airport_icao", sa.String(4)),
        sa.Column("dep_time", sa.Time()),
        sa.Column("arr_time", sa.Time()),
        sa.Column("duration", sa.Time()),
        sa.Column("departure_datetime_utc", sa.DateTime(timezone=True)),
        sa.Column("arrival_datetime_utc", sa.DateTime(timezone=True)),
        sa.Column("arrival_date", sa.Date(), index=True),
        sa.Column("airline", sa.String(100)),
        sa.Column("aircraft", sa.String(100)),
        sa.Column("registration", sa.String(20), index=True),
        sa.Column("seat_number", sa.String(10)),
        sa.Column("seat_type", sa.String(20)),
        sa.Column("flight_class", sa.String(20)),
        sa.Column("flight_reason", sa.String(20)),
        sa.Column("note", sa.String(500)),
        sa.UniqueConstraint("import_batch_id", "row_index", name="uq_flight_batch_row"),
    )

    op.create_table(
        "candidate_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_photo_id", sa.String(100), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("thumbnail_url", sa.String(500)),
        sa.Column("full_image_url", sa.String(500)),
        sa.Column("registration", sa.String(20), nullable=False, index=True),
        sa.Column("airport_code", sa.String(4)),
        sa.Column("photo_date", sa.Date(), index=True),
        sa.Column("photographer", sa.String(200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("source", "source_photo_id", name="uq_source_photo"),
    )

    op.create_table(
        "flight_photo_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "flight_id",
            sa.Integer(),
            sa.ForeignKey("flights.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "photo_id",
            sa.Integer(),
            sa.ForeignKey("candidate_photos.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("match_score", sa.Integer(), nullable=False, index=True),
        sa.Column("match_reasons", postgresql.JSONB(), default={}),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("flight_id", "photo_id", name="uq_flight_photo"),
    )

    op.create_table(
        "user_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "match_id",
            sa.Integer(),
            sa.ForeignKey("flight_photo_matches.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "scrape_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("registration", sa.String(20), nullable=False, index=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), default="pending"),
        sa.Column("priority", sa.Integer(), default=0),
        sa.Column("photos_found", sa.Integer(), default=0),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True)),
        sa.Column("next_scrape_after", sa.DateTime(timezone=True), index=True),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False, index=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("registration", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("photos_found", sa.Integer(), default=0),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("scrape_runs")
    op.drop_table("scrape_jobs")
    op.drop_table("user_decisions")
    op.drop_table("flight_photo_matches")
    op.drop_table("candidate_photos")
    op.drop_table("flights")
