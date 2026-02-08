import logging
import re
from datetime import datetime

from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedPhoto
from app.scrapers.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_ICAO_RE = re.compile(r"- ([A-Z]{4})(?:,|\s)")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class JetPhotosScraper(BaseScraper):
    source_name = "jetphotos"

    def __init__(self):
        self.rate_limiter = RateLimiter("jetphotos.com", max_requests=10, window_seconds=60)
        self.base_url = "https://www.jetphotos.com"

    async def scrape_registration(self, registration: str, airport_codes: set[str] | None = None) -> list[ScrapedPhoto]:
        photos: list[ScrapedPhoto] = []
        reg_clean = registration.replace("-", "").upper()

        await self.rate_limiter.acquire()
        url = f"{self.base_url}/registration/{reg_clean}"

        try:
            resp = curl_requests.get(
                url,
                impersonate="chrome",
                timeout=30,
            )
            if resp.status_code == 403:
                raise PermissionError(
                    "JetPhotos blocked by Cloudflare (403). "
                    "This site requires browser-based access."
                )
            if resp.status_code != 200:
                logger.warning(f"JetPhotos returned {resp.status_code} for {url}")
                return photos
        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"JetPhotos request failed: {e}")
            return photos

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(".result[data-photo]")

        for card in cards:
            try:
                photo = self._parse_card(card, registration)
                if photo:
                    photos.append(photo)
            except Exception as e:
                logger.debug(f"Failed to parse JetPhotos card: {e}")
                continue

        logger.info(f"JetPhotos: found {len(photos)} photos for {registration}")
        return photos

    def _parse_card(self, card, registration: str) -> ScrapedPhoto | None:
        photo_id = card.get("data-photo")
        if not photo_id:
            return None

        source_url = f"{self.base_url}/photo/{photo_id}"

        # Thumbnail
        img = card.select_one(".result__photo")
        thumb_url = None
        if img:
            src = img.get("src", "")
            if src.startswith("//"):
                src = "https:" + src
            if "cdn.jetphotos.com" in src:
                thumb_url = src

        # Photographer
        photographer = None
        ph_el = card.select_one(".result__infoListText--photographer a")
        if ph_el:
            photographer = ph_el.get_text(strip=True)

        # Parse desktop info section for date
        photo_date = None
        airport_code = None

        desktop = card.select_one(".desktop-only.desktop-only--block")
        if desktop:
            for li in desktop.select("li"):
                text = li.get_text(" ", strip=True)
                if text.startswith("Photo date:"):
                    date_match = _DATE_RE.search(text)
                    if date_match:
                        try:
                            photo_date = datetime.strptime(date_match.group(), "%Y-%m-%d").date()
                        except ValueError:
                            pass

        # Location is in the info2 section
        info2 = card.select_one(".result__section--info2-wrapper")
        if info2:
            for li in info2.select("li"):
                text = li.get_text(" ", strip=True)
                if text.startswith("Location:"):
                    icao_match = _ICAO_RE.search(text)
                    if icao_match:
                        airport_code = icao_match.group(1)

        return ScrapedPhoto(
            source="jetphotos",
            source_photo_id=photo_id,
            source_url=source_url,
            thumbnail_url=thumb_url,
            full_image_url=None,
            registration=registration,
            airport_code=airport_code,
            photo_date=photo_date,
            photographer=photographer,
        )
