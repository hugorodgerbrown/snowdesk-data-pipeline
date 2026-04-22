# CLAUDE.md — Snowdesk

## Project overview

Django-based data pipeline that fetches SLF (Swiss Institute for Snow and
Avalanche Research) avalanche bulletins from the CAAML API, stores them,
and renders them on a dashboard. The frontend uses HTMX for dynamic
updates without a full JavaScript framework.

## Linear workflow

Linear (team prefix `SNOW-`) is the issue source of truth. The Linear MCP
server is connected in both Claude Chat and Claude Code — the same
workspace, just different surfaces. **Nothing of substance lives only in
a chat window.** If it matters, it lives in the Linear issue.

### Status lifecycle

Every ticket ends up passing through these states. A ticket may enter
the lifecycle at `Backlog`, `Todo`, or `Ready for dev` depending on
which entry point produced it (see **Entry points** below). Each
transition is tied to a concrete event so status reflects reality
without manual nudging:

| Status         | Transition trigger                                        | Who moves it |
|----------------|-----------------------------------------------------------|--------------|
| `Backlog`      | Issue created, not yet triaged                            | Human / Chat (MCP) |
| `Todo`         | Ready to be picked up, not yet scoped                     | Human / Chat (MCP) |
| `Ready for dev`| Scoping comment posted; approach settled                  | Chat (MCP)   |
| `In Progress`  | Feature branch pushed to GitHub                           | GitHub integration |
| `In Review`    | PR opened against `main`                                  | GitHub integration |
| `Done`         | PR merged                                                 | GitHub integration |

The GitHub–Linear integration handles `In Progress`, `In Review`, and
`Done` automatically when the branch name or PR body references
`SNOW-xxx`. Chat writes to Linear via MCP when creating tickets, posting
scoping comments, and moving status up to `Ready for dev`; it does not
touch the post-commit states.

### Entry points

There are two ways a ticket reaches `Ready for dev`. Both end with a
scoped Linear issue — which is what Code consumes. Code doesn't care
which path produced it.

#### Path A — Ticket first, then scope

The ticket already exists in Linear (you created it from the UI, or it
came out of a planning session, or a previous Chat spawned it). Open a
Chat session and pull it:

> "Pull SNOW-42 and let's scope it."

Discuss the approach — data model changes, edge cases, the Django view
vs HTMX partial split, service boundaries, test shape. When the
approach is settled:

> "Post the scope as a comment on SNOW-42 and move it to Ready for dev."

Chat appends the scoping comment via MCP (see **Scoping comment
contract** below) and moves status.

#### Path B — Chat first, then tickets

A conversation exposes work that should become one or more tickets.
This is common: you're discussing a page redesign, a refactor, or a
bug you just noticed, and the discussion naturally decomposes into
distinct pieces of work. In this mode Chat creates the tickets *in
Linear* — not in a bulleted list in the chat window — so they enter
the same system with the same shape as every other ticket.

Two sub-patterns:

1. **Single ticket from a single discussion.** When scope is already
   clear from the conversation:

   > "Create a Linear ticket for this — title: 'Add region search
   > autocomplete'. Scope it directly into Ready for dev using what
   > we've just discussed."

   Chat creates the issue *and* posts the scoping comment in one go,
   then moves it to `Ready for dev`. No intermediate `Todo` stop.

2. **Multiple tickets from one discussion.** When the conversation
   decomposes into several pieces of work:

   > "Emit Linear tickets for the work we just scoped. One per
   > independently-shippable unit. Scope each one and set status:
   > anything with a settled approach goes to Ready for dev, anything
   > still fuzzy goes to Todo with a note on what's missing."

   Chat creates each issue via MCP, assigns labels and priority,
   writes the scoping comment on the ones with settled approach, and
   reports back a summary:

   > Created SNOW-102 (Ready for dev), SNOW-103 (Ready for dev),
   > SNOW-104 (Todo — needs decision on cache strategy).

**Rules for chat-spawned tickets:**

- One ticket per independently-shippable unit of work. Don't bundle
  "add feature + refactor the surrounding module" into one ticket —
  that's two.
- Title, label, priority, and a one-paragraph description are
  mandatory at creation time. The scoping comment goes on top, not
  instead.
- If the approach for a given ticket isn't settled in the discussion,
  create it at `Todo` with a note naming the open question. Don't
  promote it to `Ready for dev` just because the adjacent tickets are
  ready — that's how underspecified work leaks into implementation.
- After Chat reports the list of created tickets, **verify in Linear**
  that they look right before handing any of them to Code. The chat
  window is not the source of truth; Linear is.

### Scoping comment contract

Whichever entry point produced the ticket, the scoping comment Chat
writes to Linear has the same shape. This is the handoff artefact —
Code reads it on pickup and inherits full context without re-scoping.

- **Approach** — 2–4 sentences on the chosen solution.
- **Touch list** — files/modules expected to change.
- **Tests** — what will be covered.
- **Open questions** — anything still undecided. If non-empty, the
  ticket stays at `Todo`; only a clean scoping comment (no open
  questions) moves the ticket to `Ready for dev`.

### Implement in Claude Code

In the Snowdesk repo, open Claude Code and say:

> "Implement SNOW-42."

Code's expected sequence:

1. **Fetch** the issue and all comments via the Linear MCP server —
   including the scoping comment. Do not start work without reading
   the scoping comment; if it's missing, stop and ask the user to
   scope in Chat first.
2. **Create a branch** named `feature/SNOW-42-short-kebab-description`
   off the latest `main`. Keep the slug under ~40 chars; it appears in
   the branch list and PR title.
3. **Push the branch** to GitHub immediately (empty or with a first
   commit). Pushing a branch whose name contains `SNOW-42` is what
   triggers the Linear integration to move the ticket to `In Progress`
   — do this early so status reflects "work has started" accurately.
4. **Implement** the work on that branch, following the conventions in
   this file (render-model shape, management-command design, i18n
   rules, test structure, etc.).
5. **Run `poetry run tox`** and fix every failure before opening the PR
   (see the "Local CI — always run tox" section). Run `npm run lh` for
   any change touching a public page.
6. **Open a PR** (see next section).

### Open the PR

PR title format: `SNOW-42: short imperative summary` (matches the
branch, minus the slug fluff — e.g. `SNOW-42: Add region search
autocomplete`).

PR body must include:

```markdown
Closes SNOW-42

## What
One-paragraph summary of the change.

## Why
Link back to the scoping comment on the Linear issue. One line on the
motivation if not obvious from the title.

## How
Bullet list of the notable implementation choices — anything a reviewer
would otherwise have to reverse-engineer from the diff.

## Testing
- What was added/changed in tests.
- Any manual verification done (URLs hit, management commands run).

## Screenshots / Lighthouse
For any change touching a public page: before/after screenshots and a
note on the latest `npm run lh` scores.
```

The `Closes SNOW-42` magic comment in the PR body is what closes the
Linear ticket on merge — do not omit it. The `In Review` transition is
triggered by opening the PR (Linear watches for `SNOW-xxx` in the
branch name or PR body).

### After merge

The Linear integration moves the ticket to `Done` when the PR merges
into `main`. No manual action required.

### Branch and commit conventions

- Branch name: `feature/SNOW-xxx-short-description` for features,
  `fix/SNOW-xxx-short-description` for bug fixes,
  `chore/SNOW-xxx-short-description` for tooling/infra.
- Commit subject prefix: `SNOW-xxx:` — keeps the ticket reference in
  the git log even after squash-merge rewrites the PR title.
- One ticket per branch. If implementation reveals work that needs
  its own ticket (newly discovered, not originally scoped), create
  it via Path B from Chat — don't piggyback onto the current branch.

### When Code should stop and ask

- Scoping comment missing on the ticket → ask the user to scope in Chat
  first. Propose the scope if possible, and ask the user to confirm or
  amend.
- Tests fail after implementation and the fix isn't obvious → report
  the failure and stop, don't paper over it.
- The implementation reveals the scope was wrong → post a comment on
  the Linear issue explaining what changed and why, then ask the user
  whether to proceed, re-scope, or split into a follow-up ticket.

## Architecture

```
config/          Django project settings (split base/development/production)
pipeline/        Core app: models, views, services, management commands
  services/      Pure-function modules for fetching and processing SLF bulletins
  management/    Django management commands (fetch_bulletins, rebuild_render_models)
  templates/     Django templates; partials/ holds HTMX fragment responses
subscriptions/   Signed-token subscription flow: Subscriber and Subscription models
  services/      token.py (TimestampSigner) and email.py (account-access sending)
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
- Management commands live in `pipeline/management/commands/`. See the
  **Management command design** section below for the full convention
  (sensible defaults, dry-run-by-default, confirmation prompts).
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

**Day character**: `compute_day_character(render_model)` is a pure function that classifies a render model into one of five labels (`"Stable day"`, `"Manageable day"`, `"Hard-to-read day"`, `"Widespread danger"`, `"Dangerous conditions"`). Empty `traits` → `"Stable day"` immediately.

**Services**:
- `pipeline/services/render_model.py` — `build_render_model()`, `compute_day_character()`, `RenderModelBuildError`, `RENDER_MODEL_VERSION`.
- `pipeline/services/data_fetcher.py` — `upsert_bulletin` calls `build_render_model` inline (never via a signal); increments `run.records_failed` on `RenderModelBuildError`.

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
   for the common case (e.g. `fetch_bulletins` defaults to a read-only
   walk from `SEASON_START_DATE` to today). Required
   positional arguments are a smell — prefer optional flags with
   defaults derived from context (current date, settings, etc.).

2. **Never alter data by default — dry-run is the default.** A command
   invoked with no arguments must not write to the database, send mail,
   or call out to a paid/rate-limited external service. The user (or a
   script) must take an **explicit** step to commit changes.

3. **Pick one of the two safe shapes** — be consistent within a command,
   and ideally across the project:

   **Option A (preferred for new commands): explicit `--commit`.**
   Drop `--dry-run` entirely. The command is read-only by default;
   passing `--commit` is the only way to persist changes. This makes
   the safe path the short path and the destructive path the verbose one.

   **Option B: keep `--dry-run`, but require confirmation when absent.**
   If you keep the existing `--dry-run` flag, the command must prompt
   the user (`Proceed? [y/N]`) before writing when `--dry-run` is not
   passed. For unattended runs (cron, APScheduler, CI), accept a
   `--no-input` flag that skips the prompt. Production callers must
   pass `--no-input` explicitly — never default it on.

   Don't mix shapes within one command (e.g. `--commit` *and*
   `--dry-run`) — pick one and document it in the command's `help`.

4. **Always implement `--verbosity`** (Django gives this for free via
   `BaseCommand` — just respect it in log calls).

5. **Exit non-zero on failure.** Any unhandled error, or a partially
   failed batch (`records_failed > 0`), must surface as a non-zero exit
   so cron/CI can detect it.

## Management commands

`fetch_bulletins` is the single entry point for fetching SLF bulletins —
it supersedes the old `fetch_data` and `backfill_data` commands and
follows the **Management command design** convention (read-only by
default; opt in to writes with `--commit`).

```bash
# Read-only walk, start date derived from DB:
#   - populated DB: (latest bulletin valid_from day) → today
#                   (same-day overlap so morning-updates / prior-evening
#                    re-issues are refetched; duplicates are ignored)
#   - empty DB:     SEASON_START_DATE → today (first-run backstop)
# Useful as a "what would happen?" probe before committing.
poetry run python manage.py fetch_bulletins

# Persist the same gentle-default window.
poetry run python manage.py fetch_bulletins --commit

# Single day (typical one-off shape).
poetry run python manage.py fetch_bulletins --date 2024-06-15 --commit

# Explicit window — overrides the smart default.
poetry run python manage.py fetch_bulletins \
    --start-date 2024-01-01 --end-date 2024-12-31 --commit

# Re-pull existing rows.
poetry run python manage.py fetch_bulletins --commit --force

# Flags:
#   --start-date YYYY-MM-DD  default: latest DB bulletin's valid_from day,
#                            or settings.SEASON_START_DATE when the DB is empty.
#   --end-date   YYYY-MM-DD  default: today (UTC)
#   --date       YYYY-MM-DD  shortcut for --start-date == --end-date
#                            (mutually exclusive with the range flags)
#   --commit                 persist; omit for a read-only run
#   --force                  upsert existing bulletins instead of skipping

# Rebuild the render model on stale bulletins (render_model_version < RENDER_MODEL_VERSION).
# Read-only by default — pass --commit to persist (same convention as fetch_bulletins).
# On --commit, also refreshes RegionDayRating rows for every (region, day)
# covered by the rebuilt bulletins — pass --skip-day-ratings to suppress.
poetry run python manage.py rebuild_render_models           # read-only
poetry run python manage.py rebuild_render_models --commit  # persist (+ day ratings)

# Flags:
#   --commit                 persist; omit for a read-only run
#   --all                    rebuild every row regardless of version
#   --bulletin-id <id>       rebuild a single bulletin
#   --batch-size N           override default batch size (500)
#   --skip-day-ratings       skip the trailing RegionDayRating refresh

# Compare SQL query counts against the committed baseline (SNOW-13).
# Read-only by default — --commit rewrites perf/query_counts.txt.
poetry run python manage.py monitor_query_counts           # CI / local gate
poetry run python manage.py monitor_query_counts --commit  # accept new counts
```

`SEASON_START_DATE` is read from the environment in
`config/settings/base.py` (default: `2025-11-01`) and is the first-run
backstop: a bare invocation against an empty DB captures the full
snowpack build-up. Once the DB has bulletins, `fetch_bulletins` prefers
the gentler default of "start at the latest bulletin's `valid_from` day"
so scheduled runs only re-walk a small same-day overlap (duplicates are
ignored downstream — it's the fetch that's being optimised).

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

## Internationalisation

Settings (`config/settings/base.py`):
- `LANGUAGE_CODE = "en-gb"` — British English, matching the project spelling convention.
- `LANGUAGES = [("en", "English")]` — single-language catalogue for now.
- `LOCALE_PATHS = [BASE_DIR / "locale"]` — `.po` / `.mo` files live under `locale/en/LC_MESSAGES/`.

**Template strings**: use `{% load i18n %}` at the top of every template that wraps strings.
- `{% trans "string" %}` for short single-line strings without variables.
- `{% blocktrans with var=value %}...{{ var }}...{% endblocktrans %}` for strings with
  template variables.
- `{% blocktrans trimmed %}` when spanning multiple lines.

**Python strings**:
- `from django.utils.translation import gettext_lazy as _` at module scope for import-time
  strings (dict values, `TextChoices` labels, render model labels).
- `from django.utils.translation import gettext as _` inside functions for request-time
  strings (view helpers, template tag filters).
- Always use `%`-formatting with named placeholders for translatable strings that
  contain variables: `_("above %(bound)s") % {"bound": lower_fmt}`. Never f-strings —
  `xgettext` cannot extract them.

**Do NOT wrap**:
- SLF bulletin prose (`|snowdesk_html` / `|safe` content arriving from the API).
- Logging messages — these are operator-facing, not user-facing.
- `_TITLE_FALLBACK` values in `render_model.py` — persisted to the DB JSON field as plain strings.
- `pipeline/templates/pipeline/*` — staff-only ops UI, English-only by design.
- `static/js/map.js` strings — flagged with `// i18n: translatable` comments for a future
  JS-i18n phase; do not wrap them yet.

**Adding new strings**: after adding any user-facing string, run:
```bash
poetry run python manage.py makemessages -l en --no-location
```

**File tracking**: `.po` files are checked in; `.mo` files are gitignored. We do **not**
run `compilemessages` in CI or on deploy while the catalogue is English-only — every
`msgstr` is empty, so Django falls back to the `msgid` at render time and no compiled
binary is needed. Re-enable the compile step (and install `gettext` on the build
container) when DE/FR/IT translations are added.

**System requirement**: the `gettext` system package is only needed locally for
`makemessages` / `compilemessages` (`brew install gettext` on macOS). Not required for
deploy.

## Code style

- `ruff` for linting and formatting (includes import sorting).
- `pre-commit` hooks enforce these on commit.
- Do not suppress linting warnings with `# noqa` unless there is a good reason,
  and always leave a comment explaining why.
- Ensure that all function arguments are typed, except *args and **kwargs

### The Zen of Python

Guiding principles for writing Python in this codebase (Tim Peters,
`import this`):

- Beautiful is better than ugly.
- Explicit is better than implicit.
- Simple is better than complex.
- Complex is better than complicated.
- Flat is better than nested.
- Sparse is better than dense.
- Readability counts.
- Special cases aren't special enough to break the rules.
- Although practicality beats purity.
- Errors should never pass silently.
- Unless explicitly silenced.
- In the face of ambiguity, refuse the temptation to guess.
- There should be one — and preferably only one — obvious way to do it.
- Although that way may not be obvious at first unless you're Dutch.
- Now is better than never.
- Although never is often better than *right* now.
- If the implementation is hard to explain, it's a bad idea.
- If the implementation is easy to explain, it may be a good idea.
- Namespaces are one honking great idea — let's do more of those!

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

## Query-count monitoring (SNOW-13)

Per-page SQL query counts are tracked in `perf/query_counts.txt` — a
committed plain-text file with one `<name> <count>` pair per monitored
URL. The Lighthouse CI workflow runs `manage.py monitor_query_counts`
(read-only) after loading fixtures; any mismatch against the baseline
fails the check, so a reviewer sees the delta in the PR diff the same
way they see a Lighthouse-score delta.

**Two surfaces**:

- `pipeline.middleware.QueryCountMiddleware` attaches an
  `X-DB-Query-Count` header to every response when
  `settings.QUERY_COUNT_HEADER_ENABLED` is truthy — on in
  `development` and `perf`, off in `production`. Useful for ad-hoc
  measurement: open DevTools → Network and read the header.
- `manage.py monitor_query_counts` measures the same counts for a
  fixed URL list via the Django test client and compares / writes the
  `perf/query_counts.txt` baseline.

**Adding a new monitored URL**: append a `(name, url)` tuple to
`MONITORED_URLS` in `pipeline/management/commands/monitor_query_counts.py`,
then run `poetry run python manage.py monitor_query_counts --commit` to
seed the new baseline row.

**When the count legitimately changes** (new feature touches more of
the DB, new prefetch, etc.): run `--commit` and include the
`perf/query_counts.txt` delta in the same PR so reviewers can sanity-
check the new number.

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
