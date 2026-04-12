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
poetry run python manage.py fetch_data             # today
poetry run python manage.py fetch_data --date 2024-06-15
poetry run python manage.py backfill_data --start-date 2024-01-01 --end-date 2024-12-31
```

## Stack

- **Python / Django** — data pipeline, models, views
- **Tailwind CSS v4** — compiled via `@tailwindcss/cli` from `src/css/main.css`
  to `static/css/output.css`
- **HTMX** — dynamic updates on the pipeline dashboard
- **Poetry** — Python dependency management
- **WhiteNoise** — static file serving in production

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
