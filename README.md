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

## Data source

SLF CAAML bulletin list API (public, no auth required). Bulletins are stored
as GeoJSON Feature envelopes wrapping the raw CAAML payload.

```bash
poetry run python manage.py fetch_bulletins                       # read-only (no writes)
poetry run python manage.py fetch_bulletins --commit              # season-to-date
poetry run python manage.py fetch_bulletins --date 2024-06-15 --commit
poetry run python manage.py fetch_bulletins \
    --start-date 2024-01-01 --end-date 2024-12-31 --commit
```

## Stack

- **Python / Django** — data pipeline, models, views
- **Tailwind CSS v4** — compiled via `@tailwindcss/cli` from `src/css/main.css`
  to `static/css/output.css`
- **HTMX** — dynamic fragments on the public site (bulletin calendar, subscription region search)
- **MapLibre GL** — interactive choropleth on the public map
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
poetry run pytest                    # run tests
poetry run tox                       # full CI (lint, type-check, test)
```

See [CODING_STANDARDS.md](CODING_STANDARDS.md) for conventions and
[CLAUDE.md](CLAUDE.md) for detailed development guidance.

---

*SnowDesk is a personal project built to find its audience and serve it well.
Feedback, corrections, and conversations are welcome.*
