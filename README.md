# Tailspotted

Import your flight log from [my.flightradar24.com](https://my.flightradar24.com), then automatically find airplane spotter photos matching your flights by tail number, airport, and date.

## Features

- **CSV Import**: Upload your FlightRadar24 flight log export with automatic deduplication on re-upload
- **Smart itineraries**: Correctly computes arrival dates for red-eyes, trans-oceanic, and date line crossings using timezone-aware math
- **Multi-source scraping**: Searches four airplane photography sites for photos matching your aircraft registrations
- **Smart matching**: Scores candidates by registration + airport + date proximity (0–100), with ICAO/IATA normalization
- **Review queue**: Approve or reject candidate photos with keyboard navigation (arrow keys) and deep linking from flight details
- **Photo library**: Browse your approved photos, filterable by year
- **Queue monitor**: Slide-out panel with live stats, pause/resume, concurrency control, and ETA
- **Background processing**: ARQ worker with rate limiting, automatic rescans, and retry for failed jobs

## Quick Start

```bash
docker compose up
```

Open [http://localhost:3981](http://localhost:3981) and upload your CSV.

## Architecture

| Container | Role |
|-----------|------|
| **web** | FastAPI + HTMX frontend (port 3981) |
| **worker** | ARQ async task queue for scraping |
| **postgres** | Flight and photo data |
| **redis** | Job queue + rate limiting |

## Tech Stack

- FastAPI, Jinja2, HTMX
- SQLAlchemy (async) + Alembic
- ARQ (async Redis queue)
- httpx + BeautifulSoup for scraping
- PostgreSQL + Redis
- Docker Compose

## CSV Format

Expects the export from my.flightradar24.com (blank first line, then header row with columns: Date, Flight number, From, To, Dep time, Arr time, Duration, Airline, Aircraft, Registration, etc.).

## Photo Sources

| Source | Status | Notes |
|--------|--------|-------|
| [Airliners.net](https://www.airliners.net) | Working | Primary source. Returns photos with airport codes and dates. |
| [Airplane-Pictures.net](https://airplane-pictures.net) | Working | Uses advanced search API to filter by registration and airport. Has hotlink protection on thumbnails. |
| [Planespotters.net](https://www.planespotters.net) | Working | Returns photos with airport metadata. |
| [JetPhotos](https://www.jetphotos.com) | Working | Uses `curl_cffi` with browser impersonation to bypass Cloudflare protection. |

## How It Works

1. **Upload** your FlightRadar24 CSV export at `/upload`
2. The app parses flights, computes arrival dates/timezones, and stores them in Postgres
3. **Scrape jobs** are created for each unique aircraft registration across all configured sources
4. The ARQ background worker processes jobs with per-domain rate limiting (30 req/60s)
5. Scraped photos are **scored** against your flights — registration match, airport match (departure or arrival), and date proximity all contribute to a 0–100 score
6. Photos above the score threshold appear in the **review queue** for you to approve or reject
7. Approved photos land in your **photo library**
