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
```

In local development, run `fetch_bulletins` with `--local-mirror` so you
don't hit the live APIs. The SLF mirror replays
`bulletins/local_mirrors/slf_archive.ndjson` via `/dev/slf-mirror/`. Full
command catalogue: [docs/management-commands.md](docs/management-commands.md).

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

# Weather (drives the bulletin header — WMO bucket + day/night state)
# Fetch over the default window (latest snapshot → today); or pass --start/--end.
uv run python manage.py fetch_weather --commit
uv run python manage.py fetch_weather --start 2024-11-01 --end 2025-05-01 --commit
```

## Stack

- **Python / Django** — data pipeline, models, views, split across six
  apps: `core` (abstract `BaseModel`, middleware), `regions` (region and
  resort reference data), `bulletins` (SLF / ALBINA / Météo-France
  ingestion + render model + weather), `accounts` (signed-token
  email flow + custom user model), `public` (bulletin site), `config`
  (split settings)
- **Tailwind CSS v4** — compiled via `@tailwindcss/cli` from `src/css/main.css`
  to `static/css/output.css`
- **HTMX** — dynamic fragments on the public site (bulletin calendar, subscription region search)
- **MapLibre GL** — interactive choropleth on the public map
- **PWA shell** — service worker + offline page so an open bulletin
  stays readable on a flaky lift queue
- **Render cron** — runs the `fetch_bulletins` management command on a schedule; the pipeline itself is just Django code, no in-process scheduler
- **uv** — Python dependency management
- **WhiteNoise** — static file serving in production

## What you'll see

Per-region bulletin pages built around a unified header (region name,
issued date, weather hero, danger headline) over a **day-windows** panel
that splits morning and afternoon ratings, with a per-region **calendar**
for backwards navigation through past bulletins. The interactive `/map/`
page lets you scrub through the season — drag the bottom-bar slider or
hit play to watch the choropleth animate from November to May — and
opens a per-region drawer with today's (or any scrubbed-to date's)
bulletin on click. Resort markers overlay the choropleth so it's easy
to find a specific area. Bulletins are sourced daily from SLF, ALBINA,
and Météo-France.

## Testing

```bash
uv run tox -e test               # run tests with coverage (mirrors CI)
uv run tox                       # full CI (fmt, lint, mypy, django-checks, test)
```

See [CODING_STANDARDS.md](CODING_STANDARDS.md) for conventions and
[CLAUDE.md](CLAUDE.md) for detailed development guidance.

---

*SnowDesk is a personal project built to find its audience and serve it well.
Feedback, corrections, and conversations are welcome.*
