import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedPhoto
from app.scrapers.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_AIRPORT_RE = re.compile(r"\(([A-Z]{3})\s*/\s*([A-Z]{4})\)")


class PlanespottersScraper(BaseScraper):
    source_name = "planespotters"

    def __init__(self):
        self.rate_limiter = RateLimiter("planespotters.net", max_requests=30, window_seconds=60)
        self.base_url = "https://www.planespotters.net"

    async def scrape_registration(self, registration: str, airport_codes: set[str] | None = None) -> list[ScrapedPhoto]:
        photos: list[ScrapedPhoto] = []

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            await self.rate_limiter.acquire()
            url = f"{self.base_url}/photos/reg/{registration}"

            try:
                resp = await client.get(url)
                if resp.status_code == 403:
                    raise PermissionError(
                        "Planespotters blocked (403). Cannot scrape from server."
                    )
                if resp.status_code != 200:
                    logger.warning(f"Planespotters returned {resp.status_code} for {url}")
                    return photos
            except (PermissionError, httpx.HTTPError):
                raise
            except Exception as e:
                logger.error(f"Planespotters request failed: {e}")
                return photos

            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select(".photo-card-clickable")

            for card in cards:
                try:
                    photo = self._parse_card(card, registration)
                    if photo:
                        photos.append(photo)
                except Exception as e:
                    logger.debug(f"Failed to parse Planespotters card: {e}")
                    continue

        logger.info(f"Planespotters: found {len(photos)} photos for {registration}")
        return photos

    def _parse_card(self, card, registration: str) -> ScrapedPhoto | None:
        photo_id = card.get("id")
        if not photo_id:
            return None

        # Source URL from data-photo-url attribute
        photo_path = card.get("data-photo-url", "")
        # Strip query params for clean URL
        clean_path = photo_path.split("?")[0]
        source_url = f"{self.base_url}{clean_path}" if clean_path else f"{self.base_url}/photo/{photo_id}"

        # Thumbnail
        img = card.select_one("img")
        thumb_url = img.get("src") if img else None

        # Photographer from the overlay
        photographer = None
        ph_span = card.select_one(".drop-shadow-lg")
        if ph_span:
            photographer = ph_span.get_text(strip=True).lstrip("\u00a9 ").strip()

        # Airport code from the airport link
        airport_code = None
        airport_link = card.select_one('a[href*="/photos/airport/"]')
        if airport_link:
            title = airport_link.get("title", "")
            airport_match = _AIRPORT_RE.search(title)
            if airport_match:
                airport_code = airport_match.group(1)  # IATA code
            else:
                # Fallback: try the link text
                text = airport_link.get_text(strip=True)
                airport_match = _AIRPORT_RE.search(text)
                if airport_match:
                    airport_code = airport_match.group(1)

        # Date from the date links
        photo_date = None
        date_links = card.select('a[href*="/photos/date/"]')
        if len(date_links) >= 3:
            # Links are: day, month, year
            day = date_links[0].get_text(strip=True)
            month = date_links[1].get_text(strip=True)
            year = date_links[2].get_text(strip=True)
            try:
                photo_date = datetime.strptime(f"{day} {month} {year}", "%d %B %Y").date()
            except ValueError:
                pass
        elif len(date_links) == 2:
            # Sometimes just month + year
            month = date_links[0].get_text(strip=True)
            year = date_links[1].get_text(strip=True)
            try:
                photo_date = datetime.strptime(f"1 {month} {year}", "%d %B %Y").date()
            except ValueError:
                pass

        return ScrapedPhoto(
            source="planespotters",
            source_photo_id=photo_id,
            source_url=source_url,
            thumbnail_url=thumb_url,
            full_image_url=None,
            registration=registration,
            airport_code=airport_code,
            photo_date=photo_date,
            photographer=photographer,
        )
