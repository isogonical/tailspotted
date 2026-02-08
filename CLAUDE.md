# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Tailspotted — a dockerized single-user web app that imports flight logs from my.flightradar24.com, scrapes airplane spotter sites for matching photos, and presents them for user approval.

## Commands

```bash
# Start everything
docker compose up --build

# Rebuild after code changes (volume-mounted, so usually just restart)
docker compose restart web worker

# Run migrations manually
docker compose exec web alembic upgrade head

# Check database
docker compose exec postgres psql -U flight -d flightphotos

# View logs
docker compose logs web --tail 50
docker compose logs worker --tail 50
```

## Architecture

- **FastAPI** backend with Jinja2 + HTMX frontend
- **PostgreSQL** for data, **Redis** for job queue + rate limiting
- **ARQ** async worker for background scraping
- 4 Docker containers: web, worker, postgres, redis
- Volume-mounted source code for hot reload

## Key Files

- `app/main.py` — FastAPI app with lifespan, routes, health check
- `app/services/csv_parser.py` — FlightRadar24 CSV import with arrival date computation
- `app/services/photo_matcher.py` — Score-based flight-photo matching (0-100)
- `app/scrapers/airlinersnet.py` — Primary working scraper
- `app/scrapers/jetphotos.py` — JetPhotos scraper (uses curl_cffi for Cloudflare bypass)
- `app/workers/scrape_worker.py` — ARQ worker with cron job
- `app/routes/` — upload, flights, photos (review), library
- `app/templates/` — Jinja2 templates with HTMX partials
