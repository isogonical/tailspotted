import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapedPhoto
from app.scrapers.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Date on detail page: "Sep 13th 2018 / 13.09.2018" — use the DD.MM.YYYY part
_DATE_RE = re.compile(r"(\d{1,2})\.(\d{2})\.(\d{4})")
# Photo ID from URL like /photo/123456/some-slug/
_PHOTO_ID_RE = re.compile(r"/photo/(\d+)")


class AirplanePicturesScraper(BaseScraper):
    source_name = "airplane_pictures"

    def __init__(self):
        self.rate_limiter = RateLimiter(
            "airplane-pictures.net", max_requests=30, window_seconds=60
        )
        self.base_url = "https://airplane-pictures.net"

    async def scrape_registration(
        self, registration: str, airport_codes: set[str] | None = None
    ) -> list[ScrapedPhoto]:
        photos: list[ScrapedPhoto] = []
        seen_ids: set[str] = set()

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
            if airport_codes:
                # Use advanced search: one query per airport code
                # Separate IATA (3-char) and ICAO (4-char) codes
                iata_codes = {c for c in airport_codes if len(c) == 3}
                icao_codes = {c for c in airport_codes if len(c) == 4}

                for code in iata_codes:
                    results = await self._search(
                        client, registration, seen_ids,
                        apiata=code,
                    )
                    photos.extend(results)

                for code in icao_codes:
                    # Skip if we already searched the IATA equivalent
                    results = await self._search(
                        client, registration, seen_ids,
                        apicao=code,
                    )
                    photos.extend(results)
            else:
                # Fallback: search by registration only
                results = await self._search(
                    client, registration, seen_ids,
                )
                photos.extend(results)

        logger.info(
            f"airplane-pictures.net: found {len(photos)} photos for {registration}"
            + (f" (searched {len(airport_codes)} airports)" if airport_codes else "")
        )
        return photos

    async def _search(
        self,
        client: httpx.AsyncClient,
        registration: str,
        seen_ids: set[str],
        apiata: str | None = None,
        apicao: str | None = None,
    ) -> list[ScrapedPhoto]:
        """Run an advanced search and fetch detail pages for results."""
        photos: list[ScrapedPhoto] = []

        await self.rate_limiter.acquire()

        form_data = {"apreg": registration}
        if apiata:
            form_data["apiata"] = apiata
        if apicao:
            form_data["apicao"] = apicao

        filter_desc = registration
        if apiata:
            filter_desc += f"/{apiata}"
        if apicao:
            filter_desc += f"/{apicao}"

        try:
            resp = await client.post(
                f"{self.base_url}/search", data=form_data,
            )
            if resp.status_code == 403:
                raise PermissionError("airplane-pictures.net blocked (403).")
            if resp.status_code != 200:
                logger.warning(
                    f"airplane-pictures.net search returned {resp.status_code} for {filter_desc}"
                )
                return photos
        except (PermissionError, httpx.HTTPError):
            raise
        except Exception as e:
            logger.error(f"airplane-pictures.net search failed for {filter_desc}: {e}")
            return photos

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(".card.ap-card")

        if not cards:
            logger.debug(f"airplane-pictures.net: no results for {filter_desc}")
            return photos

        logger.debug(f"airplane-pictures.net: {len(cards)} cards for {filter_desc}")

        # Extract detail URLs from cards
        detail_urls = []
        for card in cards:
            onclick = card.get("onclick", "")
            match = re.search(r"location\.href='([^']+)'", onclick)
            if match:
                path = match.group(1)
                photo_id_match = _PHOTO_ID_RE.search(path)
                if photo_id_match and photo_id_match.group(1) not in seen_ids:
                    detail_urls.append(f"{self.base_url}{path}")
                continue

            link = card.select_one("a[href*='/photo/']")
            if link:
                href = link.get("href", "")
                if not href.startswith("http"):
                    href = self.base_url + href
                photo_id_match = _PHOTO_ID_RE.search(href)
                if photo_id_match and photo_id_match.group(1) not in seen_ids:
                    detail_urls.append(href)

        # Fetch each detail page for metadata (date is only on detail page)
        for detail_url in detail_urls:
            try:
                photo = await self._fetch_detail(
                    client, detail_url, registration
                )
                if photo and photo.source_photo_id not in seen_ids:
                    seen_ids.add(photo.source_photo_id)
                    photos.append(photo)
            except Exception as e:
                logger.debug(
                    f"Failed to parse airplane-pictures.net detail: {e}"
                )
                continue

        return photos

    async def _fetch_detail(
        self, client: httpx.AsyncClient, url: str, registration: str
    ) -> ScrapedPhoto | None:
        photo_id_match = _PHOTO_ID_RE.search(url)
        if not photo_id_match:
            return None
        photo_id = photo_id_match.group(1)

        await self.rate_limiter.acquire()

        resp = await client.get(url)
        if resp.status_code != 200:
            logger.debug(f"airplane-pictures.net detail {resp.status_code}: {url}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        photo_date = None
        airport_code = None
        photographer = None
        thumb_url = None

        # Main photo / thumbnail
        main_img = soup.select_one('img[src*="/images/uploaded-images/"]')
        if main_img:
            thumb_url = main_img.get("src")
            if thumb_url and not thumb_url.startswith("http"):
                thumb_url = self.base_url + thumb_url

        # Info table: 3-column rows with label, icon, value
        rows = soup.select("tr")
        for row in rows:
            cells = row.select("td")
            if len(cells) < 2:
                continue

            label_text = cells[0].get_text(strip=True).lower().rstrip(":")
            value_text = cells[-1].get_text(strip=True)

            if label_text == "taken" and not photo_date:
                date_match = _DATE_RE.search(value_text)
                if date_match:
                    try:
                        day = int(date_match.group(1))
                        month = int(date_match.group(2))
                        year = int(date_match.group(3))
                        photo_date = datetime(year, month, day).date()
                    except (ValueError, OverflowError):
                        pass

            elif label_text == "iata" and not airport_code:
                code = value_text.strip().upper()
                if len(code) == 3 and code.isalpha():
                    airport_code = code

            elif label_text == "icao" and not airport_code:
                code = value_text.strip().upper()
                if len(code) == 4 and code.isalpha():
                    airport_code = code

            elif label_text == "photographer" and not photographer:
                if value_text:
                    photographer = value_text

        return ScrapedPhoto(
            source="airplane_pictures",
            source_photo_id=photo_id,
            source_url=url,
            thumbnail_url=thumb_url,
            full_image_url=None,
            registration=registration,
            airport_code=airport_code,
            photo_date=photo_date,
            photographer=photographer,
        )
