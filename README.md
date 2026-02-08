# tailspotted

**Find yourself in the wild.** Import your flight log from [my.flightradar24.com](https://my.flightradar24.com), and tailspotted automatically searches airplane spotter sites for photos of your exact aircraft, taken while you were on it — matched by tail number, airport, and date.

## Quick Start

You don't need to clone this repo. Just download the compose file and run it:

```bash
curl -O https://raw.githubusercontent.com/isogonical/tailspotted/main/docker-compose.yml
docker compose up -d
```

Open [http://localhost:3981](http://localhost:3981) and upload your FlightRadar24 CSV export. That's it.

> **Prerequisites:** [Docker](https://docs.docker.com/get-docker/) with the Compose plugin. Any system that can run Docker will work — Linux, macOS, Windows, Synology, Unraid, etc.

## How It Works

1. **Export** your flight history from [my.flightradar24.com](https://my.flightradar24.com) as CSV
2. **Upload** the CSV at `/upload` — tailspotted parses your flights and computes arrival dates with full timezone awareness (red-eyes, date line crossings, etc.)
3. **Scrape jobs** run in the background, searching four spotter photography sites for photos matching your aircraft registrations
4. Photos are **matched** to your flights by relevance — registration, airport, and date
5. **Review** candidate matches — approve or reject each one
6. Approved photos go to your **library**, organized by flight

## Features

- **CSV import** with automatic deduplication — re-upload anytime without duplicates. Handles both native FlightRadar24 format and Excel-modified CSVs.
- **Four photo sources** searched in parallel: [Airliners.net](https://www.airliners.net), [JetPhotos](https://www.jetphotos.com), [Planespotters.net](https://www.planespotters.net), [Airplane-Pictures.net](https://www.airplane-pictures.net)
- **Smart matching** with ICAO/IATA airport normalization and date proximity scoring
- **Review queue** to approve or reject candidate photos
- **Photo library** of your approved collection
- **Queue monitor** — slide-out panel with live stats, pause/resume, concurrency control, and ETA
- **Background processing** with rate limiting, automatic rescans, and retry on failure
- **Data management** — delete individual flights or reset everything from the upload page

## Architecture

Four containers, all pulled automatically from GHCR:

| Container | Image | Role |
|-----------|-------|------|
| **web** | `ghcr.io/isogonical/tailspotted` | FastAPI + HTMX frontend (port 3981) |
| **worker** | `ghcr.io/isogonical/tailspotted-worker` | ARQ async task queue for scraping |
| **postgres** | `postgres:16-alpine` | Flight and photo data |
| **redis** | `redis:7-alpine` | Job queue + rate limiting |

Data is persisted in a Docker volume (`pgdata`), so your flights and photos survive restarts and upgrades.

## Updating

```bash
docker compose pull
docker compose up -d
```

Migrations run automatically on startup.

## Configuration

The default [`docker-compose.yml`](https://github.com/isogonical/tailspotted/blob/main/docker-compose.yml) works out of the box. If you want to customize:

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `SECRET_KEY` | `change-me-in-production` | Session signing key |
| `DATABASE_URL` | *(set in compose)* | PostgreSQL connection string |
| `REDIS_URL` | *(set in compose)* | Redis connection string |

To change the port, edit the `ports` mapping in the compose file (e.g., `"8080:8000"` to use port 8080).

## CSV Format

tailspotted accepts the standard export from [my.flightradar24.com](https://my.flightradar24.com). If you've edited the file in Excel, that's fine — common date format changes (like `M/D/YY`) are handled automatically.

## Development

For local development with hot reload and source-mounted volumes:

```bash
git clone https://github.com/isogonical/tailspotted.git
cd tailspotted
docker compose -f docker-compose.dev.yml up --build
```

## License

AGPLv3 — Copyright 2026 Isogonical, LLC.
