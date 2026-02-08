from datetime import date, datetime

from pydantic import BaseModel


class CandidatePhotoOut(BaseModel):
    id: int
    source: str
    source_url: str
    thumbnail_url: str | None
    registration: str
    airport_code: str | None
    photo_date: date | None
    photographer: str | None

    model_config = {"from_attributes": True}


class MatchOut(BaseModel):
    id: int
    flight_id: int
    photo_id: int
    match_score: int
    match_reasons: dict

    model_config = {"from_attributes": True}
