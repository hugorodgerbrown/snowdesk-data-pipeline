# CLAUDE.md — Snowdesk

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
  management/    Django management commands (fetch_bulletins, rebuild_render_models)
  templates/     Django templates; partials/ holds HTMX fragment responses
subscriptions/   Signed-token subscription flow (see docs/subscriptions.md)
public/          Public-facing bulletin site
  api.py         Plain JsonResponse endpoints consumed by the map page
  api_urls.py    URL routing for /api/ (namespace: api:)
src/             Tailwind CSS source (main.css — not served directly)
static/          CSS/JS assets (includes compiled output.css)
logs/            Log files (gitignored except .gitkeep)
```

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

When a runtime dependency is added via `poetry add`, **also add it to the
relevant `deps =` block in `tox.ini`** (`test`, `django-checks`, and
`mypy` all need it; `fmt` and `lint` almost never do). Tox will not pick
up `pyproject.toml` dependencies automatically.
## Subscriptions

Users subscribe to bulletin alerts via a signed-token flow — no passwords, no third-party auth library. An inline HTMX form on bulletin pages (or the landing page) captures an email address; an account-access link is sent by email. Clicking the link activates the subscriber and opens the account page where they manage their regions. Every outbound bulletin email carries a per-region unsubscribe token so subscribers can opt out without logging in.

**Entry points** — the subscribe form is a single partial included wherever a CTA is needed:

```django
{# bulletin page — region pre-seeded #}
{% include "subscriptions/partials/subscribe_form.html" with region_id=region.region_id %}

{# landing page — no region context #}
{% include "subscriptions/partials/subscribe_form.html" %}
```

The outer wrapper is `<div id="subscribe-cta-{{ region_id|default:'global' }}">`. The form posts to `subscriptions:subscribe` with `hx-target="this"` and `hx-swap="outerHTML"` so the success card replaces the form in-place.

**URL surfaces** — all mounted under `/subscribe/` (`app_name = "subscriptions"`):

| URL | Name | Method | Purpose |
|-----|------|--------|---------|
| `/subscribe/` | `subscribe` | POST | HTMX inline subscribe form |
| `/subscribe/account/<token>/` | `account` | GET | Verify token; activate subscriber |
| `/subscribe/manage/` | `manage` | GET + POST | Email entry (unauth) or region management (auth) |
| `/subscribe/manage/remove/<region_id>/` | `remove_region` | POST | HTMX — remove one region from the authenticated subscriber |
| `/subscribe/manage/delete/` | `delete_account` | POST | HTMX — hard-delete the authenticated subscriber and cascade subscriptions |
| `/subscribe/unsubscribe/<token>/` | `unsubscribe` | GET + POST | One-click region unsubscribe |
| `/subscribe/unsubscribe-done/` | `unsubscribe_done` | GET | Post-unsubscribe confirmation page |

**Models**:
- `Subscriber(email, status, confirmed_at)` — `status` is a `TextChoices` with `pending` (address captured, not yet confirmed) and `active` (confirmed; receives emails). `confirmed_at` is stamped on first account-link click.
- `Subscription(subscriber, region)` — links a `Subscriber` to a `pipeline.Region`. `unique_together` on `(subscriber, region)`.
- Hard-delete semantics: removing all regions via the manage page or unsubscribe token cascades via `on_delete=CASCADE` to drop the `Subscriber` row.

**Tokens** — `subscriptions/services/token.py` uses Django's built-in `TimestampSigner` (no extra secret needed; derived from `settings.SECRET_KEY` + salt):

- `SALT_ACCOUNT_ACCESS` — 24h TTL (configurable via `ACCOUNT_TOKEN_MAX_AGE`). Used for account-access email links.
- `SALT_UNSUBSCRIBE` — no expiry (`max_age=None`). Embedded in bulletin emails; permanent so a subscriber can always opt out from a historical email.
- Cross-salt replay is blocked at the signing layer — a token generated with one salt cannot be verified with another.
- `generate_unsubscribe_token(email, region_id)` / `verify_unsubscribe_token(token)` — convenience wrappers that encode `{email}|{region_id}` and use `SALT_UNSUBSCRIBE`.

**Indistinguishable responses** — `subscribe_partial` returns the same byte-equal `subscribe_success.html` fragment for all three branches (new subscriber, existing-pending, existing-active). The active branch calls `send_noop_email` (generates a token + renders templates but does not call `send_mail`) to equalise CPU timing. `POST /manage/` (unauthenticated) returns the same "check your inbox" page regardless of whether the email is known. This is a security property — do not regress it without understanding why.

**Rate limiting** — `django-ratelimit`, IP-keyed, `block=False` (views check `request.limited` and return 429 manually):

| View | Rate |
|------|------|
| `subscribe_partial` | 5/min |
| `manage_view` POST (unauthenticated) | 3/min |
| `unsubscribe_view` | 10/min |

Production uses `DatabaseCache` (`LOCATION = "django_cache"`) so rate-limit counters are shared across workers. The cache table is created by `subscriptions/migrations/0003_create_cache_table.py`. Development sets `RATELIMIT_ENABLE = False` so tests are not throttled.

**Account page** — `account_view` is dual-purpose: the first click on a pending subscriber's link flips `status → active` and stamps `confirmed_at`; re-clicks within the 24h window are idempotent (no double-stamp). Stores `subscriber_uuid` in the session so the manage page skips the email-entry step for the same browser session.

**Email** — Django's standard SMTP backend. No custom backend.

- **Development**: Mailhog on `localhost:1025` (no auth, no TLS). Web inbox at `http://localhost:8025`.
- **Production**: Resend SMTP relay — `EMAIL_HOST=smtp.resend.com`, `EMAIL_PORT=587`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD=<Resend API key>`, `EMAIL_USE_TLS=True`.

**Settings** (all in `.env`):
- `ACCOUNT_TOKEN_MAX_AGE` — account-access token TTL in seconds; defaults to `86400` (24h).
- `SITE_BASE_URL` — base URL for absolute links when no request is available (e.g. management commands).
- `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` — standard Django SMTP settings.
- `DEFAULT_FROM_EMAIL` — sender address for outbound mail.

**Dropped settings** — `MAGIC_LINK_SECRET_KEY`, `MAGIC_LINK_EXPIRY_SECONDS`, `MAGIC_LINK_BASE_URL`, and `RESEND_API_KEY` are no longer referenced anywhere in the codebase. Do not look for them.

## Navigation

All public pages include a shared top nav partial at
`templates/includes/nav.html`. It renders the "Snowdesk" wordmark (always
linking home), an optional chevron-back link, and — on bulletin pages
only — a right-aligned calendar glyph that loads the monthly calendar
fragment via HTMX:

```django
{# logo only — home, map #}
{% include "includes/nav.html" %}

{# logo + back link — random_bulletins, season_bulletins #}
{% url 'public:map' as map_url %}
{% include "includes/nav.html" with back_url=map_url back_label="Map" %}

{# bulletin page — back link + calendar toggle #}
{% include "includes/nav.html" with back_url=map_url back_label="Map" calendar_region_id=region.region_id calendar_partial_url=calendar_partial_url %}
```

`calendar_region_id` and `calendar_partial_url` must be passed together.
The button issues an `hx-get` against `calendar_partial_url` and swaps the
response into `#bulletin-calendar-host` in the bulletin template.

The `<nav>` spans full viewport width so its bottom border forms an
edge-to-edge rule; inner content sits in a 640px max-width container that
aligns with the bulletin body copy. See
[`docs/nav_implementation_spec.md`](docs/nav_implementation_spec.md) for
the full spec.

## Map page and JSON API

`/map/` (`public:map`) renders a MapLibre GL JS choropleth of Swiss avalanche
regions. Tapping a region opens a bottom sheet with today's danger rating,
resort list, and a CTA to the full bulletin. The template (`public/templates/public/map.html`)
is standalone — it does not extend `base.html`. Static assets are
`static/js/map.js` and `static/css/map.css`.

The map JS reads endpoint URLs from `data-*` attributes on the `#map` element,
so `{% url %}` in the template remains the single source of truth for all three
API paths.

**Search**: the header hosts a client-side autocomplete over the regions +
resorts data already fetched at load time (no extra round-trips). Matching is
diacritic-insensitive, prefix hits rank above substring hits, and results
carry a "Region" or "Resort" badge to disambiguate cases where a resort
shares its name with its parent region (e.g. "Davos"). Selecting a result
routes through the same `selectFeature` helper used by the map click handler.
The homepage links to `/map/` via an "Explore the map →" CTA next to the
existing sample-bulletin button.

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
| `GET /api/offline-manifest/map/` | `api:offline_manifest_map` | `{version, urls[]}` — precache manifest consumed by `static/js/sw.js` (see **Offline map** below) |

`today-summaries` uses the same `_select_default_issue` helper as the bulletin
page (morning-update-wins-over-previous-evening), so the map and bulletin views
always agree on which issue to show. Regions with no covering bulletin today are
absent from the response; the map fill layer treats absence as `no_rating`.
Stale/errored render models (`version: 0`) resolve to `rating: "no_rating"`.

## Offline map (SNOW-15)

The map page ships with a "Save offline" CTA that registers a service
worker and precaches everything needed to render `/map/` without a
network connection. POC status — the UX (progress chip, failure count,
cache management) will harden under follow-up tickets.

**Pieces**:
- `static/js/sw.js` — the service worker. Cache-first fetch, chunked
  precache driven by `postMessage`, versioned-cache cleanup on activate,
  synthetic 204 fallback for uncached tile requests while offline.
- `static/js/offline.js` — the client controller. Registers the SW,
  fetches the precache manifest, forwards it to the SW, relays progress
  back into the DOM.
- `public/views.py::serve_sw` — serves `/sw.js` from the root URL path
  (required for a root-scoped SW) with
  `Service-Worker-Allowed: /` and `Cache-Control: no-cache`.
  Route registered at the project root (`config/urls.py`), not under
  `public/urls.py`, since `/sw.js` must be a sibling of `/`.
- `public/api.py::offline_manifest_map` — builds the precache manifest.
  Zero DB queries. One outbound HTTP call to OpenFreeMap's TileJSON
  endpoint (`_fetch_vector_tile_template`) to resolve the current
  versioned vector-tile URL template — without this the precached keys
  wouldn't match the URLs MapLibre actually requests at runtime and the
  cache would be silently useless. Degrades to a hard-coded fallback
  template on OFM failure so the manifest always returns something.

**Cache version** — `_OFFLINE_MANIFEST_VERSION = "map-shell-v1"`. Bump
the suffix when the manifest contents change in a way that requires
clients to re-precache; the SW's `activate` handler deletes any cache
whose name starts with `map-shell-` and doesn't match the current
version.

**Manifest contents**:
- Django shell assets — `/map/` HTML, `output.css`, `map.css`, `map.js`,
  `offline.js`, favicon.
- The three map JSON endpoints (`today-summaries`, `resorts-by-region`,
  `regions.geojson`).
- MapLibre GL JS + CSS from CDN (version pinned to `_MAPLIBRE_VERSION`
  in `api.py` — must match `public/templates/public/map.html`).
- OpenFreeMap style JSON, TileJSON, sprites (1x + 2x), glyph PBFs for
  the Noto Sans fontstacks, plus vector tiles (z5–z10) and Natural
  Earth raster tiles (z5–z6) covering the Swiss bounding box
  `_SWISS_BBOX`.

**i18n** — `sw.js` has no translatable strings (never renders UI).
`offline.js` strings are flagged with `// i18n: translatable` comments
for the future JS-i18n phase; do not wrap them yet (same convention as
`map.js`).

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

**Validation**: `build_render_model` validates against the canonical 8-token EAWS problem-type enum (`DRY_PROBLEM_TYPES | WET_PROBLEM_TYPES`) and raises `RenderModelBuildError` on unknown types, aggregation/problem set mismatches, or empty `problemTypes`. Both lists empty is a legitimate quiet-day state (no raise).

**Missing aggregation is tolerated**: when a bulletin has `avalancheProblems` but no `customData.CH.aggregation`, the builder synthesises aggregation from the problem types (grouping on `category × validTimePeriod`) rather than failing. Per the CAAML schema and our analysis (see memory: `project_aggregation_purpose.md`, `project_dry_wet_disjoint_problem_types.md`), aggregation is a display hint and dry/wet problem types are disjoint, so the synthesis is unambiguous. A warning is logged so operators can spot the upstream gap.

**On validation failure**: the caller stores `render_model = {"version": 0, "error": "...", "error_type": "..."}`. `fetch_bulletins` exits non-zero via `CommandError` when `run.records_failed > 0`. `rebuild_render_models` prints a failure summary and exits non-zero.

**Safety net**: `_get_render_model` in `public/views.py` detects a stale `render_model_version` at render time, rebuilds on the fly, and logs a warning. On `RenderModelBuildError` during the rebuild it returns an error sentinel dict (does NOT write to DB); the template renders an error card. This keeps the page functional during a backfill; the warning is the signal to run the rebuild command.

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
- **No Django signals for side effects** — side effects triggered at save time
  (e.g. building the render model) are called inline from the relevant service
  function, not via `post_save` signals. This keeps data flow explicit and
  testable.

## Calendar and RegionDayRating

The bulletin page hosts a month-grid calendar, opened from the calendar
glyph in the top nav (see **Navigation**). The calendar is a
server-rendered HTMX fragment backed by a denormalised per-(region,
date) rating table — no JSON API, no per-day render-model reads at
request time.

**Model**: `pipeline.models.RegionDayRating` — one row per
`(region, calendar day)` with:
- `min_rating` / `max_rating` — `Rating` `TextChoices`
  (`no_rating`, `low`, `moderate`, `considerable`, `high`, `very_high`).
  Equal on uniform days, unequal on variable days — the calendar tile
  renders a diagonal split fill when they differ.
- `min_subdivision` / `max_subdivision` — the `+` / `-` / `=` suffix
  from the source bulletin's aggregate `danger.subdivision`, or `""`.
- `source_bulletin` — FK to the chosen `Bulletin` (nullable on
  `no_rating` days).
- `version` — `DAY_RATING_VERSION` at compute time; bump the service
  constant when the aggregation policy changes.
- `unique_together = (region, date)`; ordering `["-date", "region__region_id"]`.

**Aggregation policy** (see `pipeline/services/day_rating.py`):
- For day X, pick the single bulletin whose `_target_day` equals X with
  the latest `valid_from`. Morning-of-X (hour < 12) naturally wins over
  prior-evening-of-(X−1) (hour ≥ 12) because its `valid_from` is later.
  Evening-of-X (hour ≥ 12) targets X+1 and is excluded.
- Aggregate *within* that bulletin's `render_model["traits"]`: map each
  trait's `danger_level` (1–5) to a rating key; `max_rating` is the
  highest, `min_rating` the lowest.
- Empty traits (quiet day) → both fall back to
  `render_model["danger"]["key"]`.
- Malformed render model (empty dict; neither `danger` nor `traits`) →
  `no_rating`.
- Only qualifying bulletins are considered: `render_model_version >=
  RENDER_MODEL_VERSION` (v0 error sentinels excluded).

**Ingest hook**: `upsert_bulletin` calls
`apply_bulletin_day_ratings(bulletin)` inline after the render model is
built — never via `post_save`. Failures are logged and ingest continues
(the bulletin is still stored; the calendar tile picks up on the next
rebuild).

**Rebuild**: `rebuild_render_models` recomputes day ratings for every
`(region, day)` covered by the rebuilt bulletins as a trailing step.
Pass `--skip-day-ratings` to suppress that step when you only want to
refresh the render models (e.g. debugging a render-model bug without
touching the calendar).

**Calendar partial**: `public.views.calendar_partial` at
`/partials/calendar/<region_id>/<year>/<month>/` (name:
`public:calendar_partial`). HTMX-only — non-HTMX requests get 400. The
fragment wraps itself in `<div id="bulletin-calendar">` so prev/next
navigation swaps the outer element with
`hx-target="#bulletin-calendar" hx-swap="outerHTML"`. Year/month are
clamped to `[SEASON_START_DATE, today]` — out-of-range navigations
degrade silently rather than 404. An optional `?date=YYYY-MM-DD`
selects a specific tile for highlight rendering.

**Route ordering**: `partials/calendar/...` is registered before
`<str:region_id>/` in [`public/urls.py`](public/urls.py). Same
top-to-bottom concern as `/map/` — don't reorder.

## Data source

SLF CAAML bulletin list API (public, no auth required):
  `https://aws.slf.ch/api/bulletin-list/caaml/{lang}/json?limit={n}&offset={n}`

The API returns bulletins in reverse chronological order and is paginated.
It does not support date filtering — the pipeline pages through results and
stops once it passes the start date boundary.

Raw bulletins are wrapped in a GeoJSON Feature envelope before storage so
that downstream consumers see `{ type: "Feature", geometry: null, properties: {…} }`.

## Management command design

These rules apply to **every** new or refactored management command.
Existing commands that pre-date these rules are being migrated; don't
copy their old shape when adding new ones.

1. **Sensible defaults — runs with no arguments.** The bare invocation
   (`poetry run python manage.py <name>`) must do the most useful thing
   for the common case. Required positional arguments are a smell —
   prefer optional flags with defaults derived from context (current
   date, settings, etc.).

2. **Never alter data by default — dry-run is the default.** A command
   invoked with no arguments must not write to the database, send mail,
   or call out to a paid/rate-limited external service. The user (or a
   script) must take an **explicit** step to commit changes.

3. **Pick one of the two safe shapes** — be consistent within a command:

   **Option A (preferred for new commands): explicit `--commit`.**
   Drop `--dry-run` entirely. The command is read-only by default;
   passing `--commit` is the only way to persist changes.

   **Option B: keep `--dry-run`, but require confirmation when absent.**
   Prompt the user (`Proceed? [y/N]`) before writing when `--dry-run`
   is not passed. For unattended runs (cron, APScheduler, CI), accept
   a `--no-input` flag that skips the prompt. Production callers must
   pass `--no-input` explicitly — never default it on.

   Don't mix shapes within one command.

4. **Always implement `--verbosity`** (Django gives this for free via
   `BaseCommand` — just respect it in log calls).

5. **Exit non-zero on failure.** Any unhandled error, or a partially
   failed batch (`records_failed > 0`), must surface as a non-zero exit
   so cron/CI can detect it.

Command catalogue and flag reference: [`docs/management-commands.md`](docs/management-commands.md).

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
- Ensure that all function arguments are typed, except `*args` and `**kwargs`.
- British English spellings (colour, behaviour, organise) — except third-party
  identifiers.

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

Template formatting is enforced by `djangofmt`, which runs as a pre-commit
hook. Always run `pre-commit run djangofmt --files <path>` after editing
templates so the hook doesn't reformat on commit.

**Before opening a PR**, run `poetry run tox` and fix every failure. For any
change touching a public page, also run `npm run lh` (see
[`docs/lighthouse.md`](docs/lighthouse.md)).

## Django coding rules

- All models inherit from `BaseModel` abstract model.
- All models have an explicit `AdminModel`.
- All models have an explicit `to_string()` method.
- All models have an explicit test Factory representation.
- All models have test coverage.
- All models have an explicit `order_by` (`created_at` by default).
- All models have a custom queryset.

### Testing

- Tests use pytest.
- Tests use FactoryBoy.
- Tests live in a top-level `tests/` directory that mirrors the source
  tree. Each module has a corresponding `test_{module_name}.py`.
- All new code must have covering tests.
- Always run tests via `poetry run tox -e test` (not a bare `pytest` call) —
  the tox env mirrors CI.
- Target 100% pass rate and 90% coverage.
- All datetime objects must have `tzinfo`.
- Always call factories with `.create()` (e.g. `RegionFactory.create(...)`) —
  never use direct instantiation (`RegionFactory(...)`). The `.create()`
  classmethod is properly typed and lets mypy infer the correct model
  return type.

## Linear workflow (summary)

Linear (team prefix `SNOW-`) is the issue source of truth. The Linear MCP
server handles ticket read/write; GitHub integration handles status
transitions once a branch is pushed.

**Branch and commit conventions:**
- Branch: `feature/SNOW-xxx-short-description` (features), `fix/SNOW-xxx-…`
  (bugs), `chore/SNOW-xxx-…` (tooling/infra).
- Commit subject prefix: `SNOW-xxx:` — keeps the ticket reference in the git
  log after squash-merge.
- One ticket per branch.

**PR title:** `SNOW-42: short imperative summary`. The body must start with
`Closes SNOW-42` — that magic comment closes the Linear ticket on merge.

**When Code should stop and ask:**
- Scoping comment missing on the ticket → ask the user to scope in Chat first.
- Tests fail after implementation and the fix isn't obvious → report and stop.
- Implementation reveals the scope was wrong → post a comment on the Linear
  issue and ask the user how to proceed.

Full lifecycle, entry points, scoping-comment contract, and PR body template:
[`docs/linear-workflow.md`](docs/linear-workflow.md).

## Feature-specific reference

Read these when working in the relevant area:

| Area | Doc |
|------|-----|
| Subscriptions (signed tokens, rate limits, email) | [`docs/subscriptions.md`](docs/subscriptions.md) |
| Render model (shape, versioning, day character) | [`docs/render-model.md`](docs/render-model.md) |
| Map page and JSON API | [`docs/map-and-api.md`](docs/map-and-api.md) |
| Internationalisation | [`docs/i18n.md`](docs/i18n.md) |
| Lighthouse CI (budgets, perf settings) | [`docs/lighthouse.md`](docs/lighthouse.md) |
| Query-count monitoring (SNOW-13) | [`docs/query-counts.md`](docs/query-counts.md) |
| Management command catalogue | [`docs/management-commands.md`](docs/management-commands.md) |
| Nav partial implementation spec | [`docs/nav_implementation_spec.md`](docs/nav_implementation_spec.md) |
