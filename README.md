# SnowDesk

Django application that fetches SLF (Swiss Institute for Snow and Avalanche
Research) avalanche bulletins from the CAAML API, stores them, and presents
them as a mobile-friendly public website.

## Quick start

```bash
cp .env.example .env          # fill in values
poetry install
npm install
poetry run python manage.py migrate

# Terminal 1: Tailwind CSS watcher
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch

# Terminal 2: Django dev server
poetry run python manage.py runserver
```

In local development, run `fetch_bulletins` against the dev mirror
(`--source local-mirror`) so you don't hit the live SLF API. The mirror
replays `bulletins/local_mirrors/slf_archive.ndjson` via `/dev/slf-mirror/`. Full
command catalogue: [docs/management-commands.md](docs/management-commands.md).

## Data source

SLF CAAML bulletin list API (public, no auth required). Bulletins are stored
as GeoJSON Feature envelopes wrapping the raw CAAML payload.

```bash
# Bulletin ingestion (dry-run by default; --commit to persist)
poetry run python manage.py fetch_bulletins --source local-mirror --commit
poetry run python manage.py fetch_bulletins --date 2024-06-15 --commit
poetry run python manage.py fetch_bulletins \
    --start-date 2024-01-01 --end-date 2024-12-31 --commit

# Render-model rebuild (after a RENDER_MODEL_VERSION bump)
poetry run python manage.py rebuild_render_models --commit

# Weather (drives the bulletin header — WMO bucket + day/night state)
poetry run python manage.py fetch_weather --commit
poetry run python manage.py backfill_weather --start 2024-11-01 --end 2025-05-01 --commit
```

## Stack

- **Python / Django** — data pipeline, models, views, split across six
  apps: `core` (abstract `BaseModel`, middleware), `regions` (region and
  resort reference data), `bulletins` (SLF ingestion + render model +
  weather), `subscriptions` (signed-token email flow + custom user
  model), `public` (bulletin site), `config` (split settings)
- **Tailwind CSS v4** — compiled via `@tailwindcss/cli` from `src/css/main.css`
  to `static/css/output.css`
- **HTMX** — dynamic fragments on the public site (bulletin calendar, subscription region search)
- **MapLibre GL** — interactive choropleth on the public map
- **PWA shell** — service worker + offline page so an open bulletin
  stays readable on a flaky lift queue
- **Render cron** — runs the `fetch_bulletins` management command on a schedule; the pipeline itself is just Django code, no in-process scheduler
- **Poetry** — Python dependency management
- **WhiteNoise** — static file serving in production

## What you'll see

Per-region bulletin pages built around a fixed **masthead** (region name,
issued date, danger headline) over a **day-windows** panel that splits
morning and afternoon ratings, with a per-region **calendar** for
backwards navigation through past bulletins. The interactive `/map/`
page lets you scrub through the season — drag the bottom-bar slider or
hit play to watch the choropleth animate from November to May — and
opens a per-region drawer with today's (or any scrubbed-to date's)
bulletin on click. Resort markers overlay the choropleth so it's easy
to find a specific area. All of it sources daily from SLF.

## Testing

```bash
poetry run tox -e test               # run tests with coverage (mirrors CI)
poetry run tox                       # full CI (fmt, lint, mypy, django-checks, test)
```

See [CODING_STANDARDS.md](CODING_STANDARDS.md) for conventions and
[CLAUDE.md](CLAUDE.md) for detailed development guidance.

---

*SnowDesk is a personal project built to find its audience and serve it well.
Feedback, corrections, and conversations are welcome.*
