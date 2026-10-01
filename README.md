# SnowDesk

Django application that fetches avalanche bulletins from SLF (Swiss
Institute for Snow and Avalanche Research), ALBINA (EUREGIO
avalanche.report), and Météo-France, stores them, and presents them as a
mobile-friendly public website.

## Quick start

```bash
cp .env.example .env          # fill in values
uv sync
npm install
uv run python manage.py migrate

# Terminal 1: Tailwind CSS watcher
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch

# Terminal 2: Django dev server
uv run python manage.py runserver

# Terminal 3: local email sink (SMTP :1025, inbox at http://localhost:8025)
mailpit                       # brew install mailpit
```

In local development, run `fetch_bulletins` with `--local-mirror` so you
don't hit the live APIs. The SLF and ALBINA mirrors replay
`apps/bulletins/local_mirrors/*.ndjson` via `/dev/slf-mirror/` and
`/dev/albina-mirror/`; the Météo-France mirror reads a `file://` URL set in
`METEOFRANCE_API_LOCAL_MIRROR_URL`. Full command catalogue:
[docs/management-commands.md](docs/management-commands.md).

## Data sources

Three avalanche bulletin providers are supported. Bulletins are stored as
GeoJSON Feature envelopes wrapping the raw CAAML payload.

- **SLF** — CAAML paginated API at `aws.slf.ch` (public, no auth required).
- **ALBINA** — EUREGIO bulletin CDN at `avalanche.report` (public, no auth required).
- **Météo-France** — DPBRA APIM (API key required; see `.env.example`).

```bash
# Bulletin ingestion (dry-run by default; --commit to persist)
# --source is required; end is always today UTC (there is no --end-date flag)
uv run python manage.py fetch_bulletins --source slf albina meteofrance --commit
uv run python manage.py fetch_bulletins --source slf --date 2024-06-15 --commit
uv run python manage.py fetch_bulletins --source slf --start-date 2024-01-01 --commit

# Render-model rebuild (after a RENDER_MODEL_VERSION bump)
uv run python manage.py rebuild_render_models --commit

# Weather (one Open-Meteo row per active Location per day; takes no date flags)
uv run python manage.py fetch_weather --commit
# Past days come from the historical API via backfill_weather or the admin action
uv run python manage.py backfill_weather --commit
```

## Stack

- **Python / Django** — data pipeline, models, views, split across fifteen
  apps under `apps/` (`core`, `locations`, `regions`, `bulletins`,
  `weather`, `accounts`, `favourites`, `observations`, `routes`, `trips`,
  `downloads`, `analytics`, `mcp_server`, `oauth`, `public`) plus `config`
  (split settings). The map in [CLAUDE.md](CLAUDE.md#architecture) says
  what each one owns
- **Tailwind CSS v4** — compiled via `@tailwindcss/cli` from `src/css/main.css`
  to `static/css/output.css`
- **HTMX** — dynamic fragments on the public site (bulletin calendar, favourites, trips, field reports)
- **MapLibre GL** — interactive choropleth on the public map
- **PWA shell** — service worker, offline page and downloadable basemap
  areas so an open bulletin and map stay readable on a flaky lift queue
- **APScheduler** — `schedule.py` registers the recurring jobs
  (`fetch_bulletins` twice an hour, `fetch_weather` four times a day, the
  nightly purges) and the `run_scheduler` command runs them as a Render
  background worker; the jobs themselves are plain management commands
- **uv** — Python dependency management
- **WhiteNoise** — static file serving in production

## What you'll see

Per-region bulletin pages built around a masthead (sub-region eyebrow,
region name, page date, share) over a **day-windows** panel that splits
morning and afternoon ratings, with a per-region **calendar** for
backwards navigation through past bulletins. The interactive `/map/` page
lets you scrub through the season — drag the bottom-bar slider or hit
play to watch the choropleth animate from November to May — and opens a
per-region panel with today's (or any scrubbed-to date's) bulletin on
click. Resorts, saved places, routes, trips, field reports and point
weather are toggleable layers on the same map, and basemap areas can be
downloaded for offline use. Bulletins are fetched twice an hour from
SLF, ALBINA, and Météo-France.

## Testing

```bash
uv run tox -e test               # run tests with coverage (mirrors CI)
uv run tox                       # every default env: fmt, lint, mypy, django-checks, the lint guards, test, js
```

See [docs/coding-standards.md](docs/coding-standards.md) for conventions and
[CLAUDE.md](CLAUDE.md) for detailed development guidance.

---

*SnowDesk is a personal project built to find its audience and serve it well.
Feedback, corrections, and conversations are welcome.*
