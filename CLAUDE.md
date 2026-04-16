# CLAUDE.md — Snowdesk Data Pipeline

## Project overview

Django-based data pipeline that fetches SLF (Swiss Institute for Snow and
Avalanche Research) avalanche bulletins from the CAAML API, stores them,
and renders them on a dashboard. The frontend uses HTMX for dynamic
updates without a full JavaScript framework.

## Architecture

```
config/          Django project settings (split base/development/production)
pipeline/        Core app: models, views, services, management commands
  services/      Pure-function modules for fetching and processing SLF bulletins
  management/    Django management commands (backfill_data, fetch_data)
  templates/     Django templates; partials/ holds HTMX fragment responses
subscriptions/   Magic-link subscription auth: Subscriber and Subscription models
  services/      token.py (PyJWT) and email.py (magic-link sending)
  templates/     Subscription flow pages and email templates
public/          Public-facing bulletin site
  api.py         Plain JsonResponse endpoints consumed by the map page
  api_urls.py    URL routing for /api/ (namespace: api:)
src/             Tailwind CSS source (main.css — not served directly)
static/          CSS/JS assets (includes compiled output.css)
logs/            Log files (gitignored except .gitkeep)
```

## Conventions

- **Header comment block** on every module describing its purpose.
- **Docstring** on every function and class.
- **Composition over inheritance** — favour passing service objects as arguments
  over deep class hierarchies.
- **Simple over complex** — no abstractions until they are needed by at least two
  callers.
- Settings are split: `config/settings/base.py`, `development.py`, `production.py`.
  Set `DJANGO_SETTINGS_MODULE` in the environment.
- Use `python-decouple` for secrets; never hard-code credentials.
- Logging is configured in `base.py` under `LOGGING`. Use `logging.getLogger(__name__)`
  in every module.
- Management commands live in `pipeline/management/commands/`. Each command has
  `--dry-run` and `--verbosity` support.
- **No Django signals for side effects** — side effects triggered at save time
  (e.g. building the render model) are called inline from the relevant service
  function, not via `post_save` signals. This keeps data flow explicit and
  testable.

## Running locally

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

## Dependency management

Use **Poetry**. `pyproject.toml` is the single source of truth; there is no
`requirements.txt`.

```bash
poetry add <package>              # add a runtime dependency
poetry add --group dev <package>  # add a dev-only dependency
poetry update                     # update all dependencies within constraints
poetry show --outdated            # list packages with newer versions available
```

The virtualenv lives at `.venv/` inside the repo — this is **by design**,
pinned via `poetry.toml` (`virtualenvs.in-project = true`). The pre-commit
mypy hook in `.pre-commit-config.yaml` invokes `.venv/bin/mypy` by
repo-relative path so the hook works identically from the CLI and from
GUI git clients (SublimeMerge, Tower, Fork, etc.) which launch git with
a minimal environment and don't inherit the user's shell PATH. Don't
change the venv location without also updating the mypy hook entry.

## Subscriptions

Users subscribe to bulletin alerts via a magic-link auth flow — no passwords.

1. User submits their email at `/subscribe/` → a JWT magic link is emailed to them.
2. Clicking the link hits `/subscribe/verify/?token=` — the JWT is validated (15-min expiry).
3. New subscribers are forwarded to `/subscribe/regions/` to pick their regions.
4. Returning subscribers are forwarded to `/subscribe/manage/` to update their regions.
5. Every outbound bulletin email contains a manage-subscription link using the same JWT mechanism.

**Models**: `Subscriber` (email, created/updated timestamps) and `Subscription` (subscriber + region FK).

**Services**:
- `subscriptions/services/token.py` — JWT generation and validation via PyJWT.
- `subscriptions/services/email.py` — renders and sends the magic-link email.

**Settings** (all required in `.env`):
- `MAGIC_LINK_SECRET_KEY` — signing secret for JWTs.
- `MAGIC_LINK_EXPIRY_SECONDS` — token TTL; defaults to `900` (15 minutes).
- `MAGIC_LINK_BASE_URL` — base URL prepended to the verify path (e.g. `https://example.com`).
- `EMAIL_BACKEND` — use `django.core.mail.backends.console.EmailBackend` in development.
- `DEFAULT_FROM_EMAIL` — sender address for outbound mail.

## Map page and JSON API

`/map/` (`public:map`) renders a MapLibre GL JS choropleth of Swiss avalanche
regions. Tapping a region opens a bottom sheet with today's danger rating,
resort list, and a CTA to the full bulletin. The template (`public/templates/public/map.html`)
is standalone — it does not extend `base.html`. Static assets are
`static/js/map.js` and `static/css/map.css`.

The map JS reads endpoint URLs from `data-*` attributes on the `#map` element,
so `{% url %}` in the template remains the single source of truth for all three
API paths.

**Route ordering**: `/map/` is registered before `<str:region_id>/` in
`public/urls.py`. Do not reorder these — Django matches URL patterns
top-to-bottom and the generic region pattern would swallow `/map/` if it
appeared first.

**JSON API** — plain `JsonResponse` views, no DRF. Mounted at `/api/` in
`config/urls.py` under the `api:` namespace (`public/api_urls.py`):

| URL | Name | Response |
|-----|------|----------|
| `GET /api/today-summaries/` | `api:today_summaries` | `{region_id: {rating, subdivision, problem, elevation, aspects, valid_from, valid_to, name}}` |
| `GET /api/resorts-by-region/` | `api:resorts_by_region` | `{region_id: [resort_name, …]}` — alphabetical; regions without resorts omitted |
| `GET /api/regions.geojson` | `api:regions_geojson` | GeoJSON FeatureCollection from `Region.boundary`; each feature has `properties.id` + `properties.name` |

`today-summaries` uses the same `_select_default_issue` helper as the bulletin
page (morning-update-wins-over-previous-evening), so the map and bulletin views
always agree on which issue to show. Regions with no covering bulletin today are
absent from the response; the map fill layer treats absence as `no_rating`.
Stale/errored render models (`version: 0`) resolve to `rating: "no_rating"`.

## Render model

Each `Bulletin` stores a pre-computed `render_model` JSONField built at ingest time so templates contain no derivation logic.

**Shape**: `{ version, danger, traits[], fallback_key_message, snowpack_structure, metadata, prose }`.
- `danger` — `{ key, number, subdivision }` resolved from `dangerRatings`.
- `traits[]` — one entry per `customData.CH.aggregation` entry; each has `{ category, time_period, title, geography, problems[], prose, danger_level }`.
  - Trait and problem ordering is taken verbatim from SLF's aggregation.
  - `category` is `"dry"` or `"wet"`, sourced directly from SLF's aggregation — not inferred.
  - `geography.source` is `"problems"` when aspects/elevation are present, or `"prose_only"` when the SLF prose comment is the only geographic description.
- `metadata` — `{ publication_time, valid_from, valid_until, next_update, unscheduled, lang }`. Timestamps are ISO 8601 strings or `None`; `unscheduled` defaults to `False`; `lang` defaults to `"en"`.
- `prose` — `{ snowpack_structure, weather_review, weather_forecast, tendency[] }`. Scalars are HTML strings or `None`. Each tendency entry has `{ comment, tendency_type, valid_from, valid_until }`.
- `snowpack_structure` (top-level) is kept alongside `prose.snowpack_structure` for backward compatibility; both hold the same value. Will be dropped in v4.

**Versioning**: `RENDER_MODEL_VERSION = 3` (in `pipeline/services/render_model.py`). Bump it and run `rebuild_render_models` whenever the output shape or builder logic changes. `BulletinQuerySet.needs_render_model_rebuild()` returns all rows with a stale version.

**Validation**: `build_render_model` validates against the canonical 8-token EAWS problem-type enum (`DRY_PROBLEM_TYPES | WET_PROBLEM_TYPES`) and raises `RenderModelBuildError` on unknown types, aggregation/problem set mismatches, empty `problemTypes`, or missing aggregation when problems exist. Both lists empty is a legitimate quiet-day state (no raise).

**On validation failure**: the caller stores `render_model = {"version": 0, "error": "...", "error_type": "..."}`. `fetch_data` and `backfill_data` exit non-zero via `CommandError` when `run.records_failed > 0`. `rebuild_render_models` prints a failure summary and exits non-zero.

**Safety net**: `_get_render_model` in `public/views.py` detects a stale `render_model_version` at render time, rebuilds on the fly, and logs a warning. On `RenderModelBuildError` during the rebuild it returns an error sentinel dict (does NOT write to DB); the template renders an error card. This keeps the page functional during a backfill; the warning is the signal to run the rebuild command.

**Day character**: `compute_day_character(render_model)` is a pure function that classifies a render model into one of five labels (`"Stable day"`, `"Manageable day"`, `"Hard-to-read day"`, `"Widespread danger"`, `"Dangerous conditions"`). Empty `traits` → `"Stable day"` immediately.

**Services**:
- `pipeline/services/render_model.py` — `build_render_model()`, `compute_day_character()`, `RenderModelBuildError`, `RENDER_MODEL_VERSION`.
- `pipeline/services/data_fetcher.py` — `upsert_bulletin` calls `build_render_model` inline (never via a signal); increments `run.records_failed` on `RenderModelBuildError`.

## Data source

SLF CAAML bulletin list API (public, no auth required):
  `https://aws.slf.ch/api/bulletin-list/caaml/{lang}/json?limit={n}&offset={n}`

The API returns bulletins in reverse chronological order and is paginated.
It does not support date filtering — the pipeline pages through results and
stops once it passes the start date boundary.

Raw bulletins are wrapped in a GeoJSON Feature envelope before storage so
that downstream consumers see `{ type: "Feature", geometry: null, properties: {…} }`.

## Management commands

```bash
# Fetch bulletins for today
poetry run python manage.py fetch_data

# Fetch for a specific date
poetry run python manage.py fetch_data --date 2024-06-15

# Backfill historical bulletins
poetry run python manage.py backfill_data --start-date 2024-01-01 --end-date 2024-12-31

# All commands accept:
#   --dry-run   fetch but do not write to the database
#   --force     upsert existing bulletins instead of skipping

# Rebuild the render model on stale bulletins (render_model_version < RENDER_MODEL_VERSION)
poetry run python manage.py rebuild_render_models

# Flags: --all (every row), --bulletin-id <id> (single row), --dry-run, --batch-size N
```

## Frontend

**Tailwind CSS v4** compiled via the `@tailwindcss/cli` package.

- Source: `src/css/main.css` — contains `@import "tailwindcss"`, `@theme` design
  tokens, and component exceptions. Lives outside `static/` so WhiteNoise never
  tries to post-process it.
- Output: `static/css/output.css` — gitignored build artifact loaded by templates.
- All styling uses Tailwind utility classes in templates. Only add custom CSS to
  `src/css/main.css` for things Tailwind cannot express (generated content,
  data-attribute selectors, raw HTML resets).

```bash
# Development (watch mode)
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch

# Production (minified)
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --minify
```

**HTMX** patterns:
- Full-page views return a complete HTML response.
- Partial/fragment views return only the inner HTML snippet; they are routed under
  `pipeline/urls.py` with a `partials/` prefix and guarded by `require_htmx`.
- Use `hx-target`, `hx-swap="innerHTML"`, and `hx-indicator` for all dynamic
  requests.

## Code style

- `ruff` for linting and formatting (includes import sorting).
- `pre-commit` hooks enforce these on commit.
- Do not suppress linting warnings with `# noqa` unless there is a good reason,
  and always leave a comment explaining why.
- Ensure that all function arguments are typed, except *args and **kwargs

## Local CI — always run tox

**`tox` is the single entry point** for running linters, type checks, Django
system checks, and the test suite locally. The tox envs declare their own
dependencies (independent of the Poetry venv), so a tox run mirrors what CI
will execute — catching the "works on my machine" class of failure before a
PR is opened.

```bash
poetry run tox                    # run every env (fmt, lint, mypy, django-checks, test)
poetry run tox -e test            # one env at a time
poetry run tox -e mypy
poetry run tox -e django-checks
poetry run tox -e fmt             # ruff format --check
poetry run tox -e lint            # ruff check
poetry run tox --recreate         # rebuild envs from scratch after a deps change
```

When a runtime dependency is added via `poetry add`, **also add it to the
relevant `deps =` block in `tox.ini`** (`test`, `django-checks`, and
`mypy` all need it; `fmt` and `lint` almost never do). Tox will not pick
up `pyproject.toml` dependencies automatically.

Template formatting is enforced by `djangofmt`, which runs as a pre-commit
hook. Always run `pre-commit run djangofmt --files <path>` (or just `pre-commit
run --all-files`) after editing templates so the hook doesn't reformat on commit.

**Before opening a pull request**, run `poetry run tox` and fix every failure.
Do not rely on CI to surface issues that tox would have caught locally.

## Lighthouse CI — accessibility, SEO, performance, best-practices

Lighthouse audits the public site on every PR and blocks merge on
regressions. Both local and CI invocations read
[`lighthouserc.json`](lighthouserc.json) for URLs, thresholds, and
assertions — keep it the single source of truth.

**Budgets** (error = blocks merge, warn = report only):
- `categories:accessibility` ≥ 0.95 — error
- `categories:seo` ≥ 0.95 — error
- `categories:performance` ≥ 0.85 — warn
- `categories:best-practices` ≥ 0.9 — warn

Mobile preset by default (no desktop override), 3 runs per URL.

**Run locally — `npm run lh`**

Requires Chrome/Chromium on the host. The script:

1. Runs `collectstatic --noinput` under `DJANGO_SETTINGS_MODULE=config.settings.perf`
   so the ManifestStaticFilesStorage manifest is populated.
2. Starts a Django server on `:8765` using `config.settings.perf` — the
   same WhiteNoise + `CompressedManifestStaticFilesStorage` + `GZipMiddleware`
   stack as production, so hashed filenames, pre-compressed assets, and
   cache headers match reality.
3. Audits the URLs in `lighthouserc.json` and writes HTML + JSON reports
   to `.lighthouseci/` (gitignored).

```bash
npm run lh          # full audit — ~90s
npm run lh:open     # opens the representative HTML report per URL (macOS)
```

**`config/settings/perf.py` is Lighthouse-only** — extends `development`,
flips `DEBUG=False`, adds WhiteNoise + GZip. Not a deploy target;
`production.py` remains the production source of truth.

**CI** — [`.github/workflows/lighthouse.yml`](.github/workflows/lighthouse.yml)
runs on every PR: loads regions/resorts/bulletin fixtures, rebuilds
render models, runs `collectstatic` under perf settings, then
`lhci autorun` with the CH-4115 bulletin URL added on top of the
config URLs. Reports upload as a 14-day GitHub Actions artifact.

**When adding a new public page**, check all of:

- `<meta name="description" content="…">` — fail-fast for SEO.
- `<link rel="icon" type="image/svg+xml" href="{% static 'favicon.svg' %}">` —
  otherwise browsers probe `/favicon.ico` and log a 404 to the console.
- Use `text-text-1`, `text-text-2`, or the `--color-eaws-*-text` tokens
  when contrast matters; `text-text-3` sits on the WCAG AA boundary
  (4.67:1 on `--color-bg`) — never dim it further with `opacity-*`.
- Keep heading order sequential (`h1 → h2 → h3`); do not skip levels.
  The reviewer agent will run `npm run lh` and flag regressions.

**Before opening a PR**: run `npm run lh` alongside `poetry run tox`
and clear both. The reviewer agent runs lh as part of its checklist.

## Django coding rules

- All models to inherit from `BaseModel` abstract model
- All models to have an explicit AdminModel
- All models to have an explicit `to_string()` method
- All models to have an explicit test Factory representation
- All models to have test coverage (see Testing section)
- All models to have an explicit `order_by` (`created_at` by default)
- All models to have a custom queryset


### Testing

- Tests to use pytest
- Tests to use FactoryBoy
- Tests in a top level directory called "tests" that then mirrors the strucuture of the source files it's testing. Each Django module should have a
corresponding test_{module_name}.py that contains the tests.
- All new code must have covering tests
- Always run tests after code changes and ensure 100% pass rate and 90% coverage.
- **Run tests via `poetry run tox -e test`** (not via a bare `pytest` call).
  The tox env mirrors CI; running pytest directly may succeed against the
  Poetry venv while CI fails on missing deps in the tox env.
- See the "Local CI — always run tox" section above for the full command set
  and the dependency-sync rule.
- All datetime objects must have tzinfo
- Always call factories with `.create()` (e.g. `RegionFactory.create(...)`) — never
  use direct instantiation (`RegionFactory(...)`). The `.create()` classmethod is
  properly typed and lets mypy infer the correct model return type.
