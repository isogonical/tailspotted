import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedPhoto
from app.scrapers.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Month names for date parsing
_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s*(\d{4})"
)
_AIRPORT_CODE_RE = re.compile(r"\(([A-Z]{3})\s*/\s*[A-Z]{4}\)")


class AirlinersNetScraper(BaseScraper):
    source_name = "airlinersnet"

    def __init__(self):
        self.rate_limiter = RateLimiter("airliners.net", max_requests=30, window_seconds=60)
        self.base_url = "https://www.airliners.net"

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
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            },
        ) as client:
            page = 1
            while page <= 5:
                await self.rate_limiter.acquire()
                url = f"{self.base_url}/search?registrationActual={registration}&page={page}"

                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(f"Airliners.net returned {resp.status_code} for {url}")
                        break
                except httpx.HTTPError as e:
                    logger.error(f"Airliners.net request failed: {e}")
                    break

                soup = BeautifulSoup(resp.text, "lxml")
                rows = soup.select(".ps-v2-results-display-detail-col")

                if not rows:
                    break

                for row in rows:
                    try:
                        photo = self._parse_row(row, registration)
                        if photo:
                            photos.append(photo)
                    except Exception as e:
                        logger.debug(f"Failed to parse airliners.net row: {e}")
                        continue

                next_link = soup.select_one('a[rel="next"]') or soup.select_one(
                    ".ps-v2-results-pagination-next a"
                )
                if not next_link:
                    break
                page += 1

        logger.info(f"Airliners.net: found {len(photos)} photos for {registration}")
        return photos

    def _parse_row(self, row, registration: str) -> ScrapedPhoto | None:
        link = row.select_one("a[href*='/photo/']")
        if not link:
            return None

        href = link.get("href", "")
        # Strip query params for clean URL
        clean_href = href.split("?")[0]
        if not clean_href.startswith("http"):
            clean_href = self.base_url + clean_href

        # Extract photo ID from URL path like /photo/.../7500047
        photo_id_match = re.search(r"/(\d+)(?:\?|$)", href)
        if not photo_id_match:
            return None
        photo_id = photo_id_match.group(1)

        # Thumbnail
        img = row.select_one("img[src*='imgproc']") or row.select_one(
            "img[data-src*='imgproc']"
        )
        thumb_url = None
        if img:
            thumb_url = img.get("src") or img.get("data-src")

        # Extract metadata from columns
        airport_code = None
        photo_date = None
        photographer = None

        cols = row.select(".ps-v2-results-col")
        for col in cols:
            text = col.get_text(" ", strip=True)

            # Location & Date column
            if "Location" in text or _AIRPORT_CODE_RE.search(text):
                airport_match = _AIRPORT_CODE_RE.search(text)
                if airport_match:
                    airport_code = airport_match.group(1)

                date_match = _DATE_RE.search(text)
                if date_match:
                    try:
                        date_str = f"{date_match.group(1)} {date_match.group(2)}, {date_match.group(3)}"
                        photo_date = datetime.strptime(date_str, "%B %d, %Y").date()
                    except ValueError:
                        pass

            # Photographer column
            if "Photographer" in text:
                # Text after "Photographer" label
                ph_text = text.replace("Photographer", "").strip()
                # Often includes country and rating, just take the name part
                if ph_text:
                    # Remove trailing numbers and country info
                    parts = ph_text.split("\n")
                    photographer = parts[0].strip() if parts else ph_text

        # Derive full-res URL: swap size suffix -6 → -0
        full_url = None
        if thumb_url:
            full_url = re.sub(r"-\d\.jpg$", "-0.jpg", thumb_url)

        return ScrapedPhoto(
            source="airlinersnet",
            source_photo_id=photo_id,
            source_url=clean_href,
            thumbnail_url=thumb_url,
            full_image_url=full_url,
            registration=registration,
            airport_code=airport_code,
            photo_date=photo_date,
            photographer=photographer,
        )
