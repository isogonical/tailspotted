from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class ScrapedPhoto:
    source: str
    source_photo_id: str
    source_url: str
    thumbnail_url: str | None
    full_image_url: str | None
    registration: str
    airport_code: str | None
    photo_date: date | None
    photographer: str | None


class BaseScraper(ABC):
    source_name: str

    @abstractmethod
    async def scrape_registration(
        self, registration: str, airport_codes: set[str] | None = None
    ) -> list[ScrapedPhoto]:
        """Scrape photos for a given registration.

        Args:
            registration: Aircraft registration (e.g. "N506DN")
            airport_codes: Optional set of IATA/ICAO codes to filter by.
                          Scrapers that support server-side filtering can use
                          this to reduce the number of results fetched.
        """
        ...
