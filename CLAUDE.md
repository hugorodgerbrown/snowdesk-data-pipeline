# CLAUDE.md — Snowdesk

## Project overview

Django-based data pipeline that fetches SLF (Swiss Institute for Snow and
Avalanche Research) avalanche bulletins from the CAAML API, stores them,
and renders them on a dashboard. The frontend uses HTMX for dynamic
updates without a full JavaScript framework.

Python 3.12 / Django 6.0 (pinned in `pyproject.toml`). If tox envs behave
oddly after a dependency change, rebuild them with `poetry run tox --recreate`.

## Architecture

```
config/          Django project settings (split base/development/production)
core/            Shared abstractions (BaseModel; abstract, no concrete tables),
                 plus HTTP-layer middleware and the monitor_query_counts command
regions/         Geographic reference data — MicroRegion / MajorRegion /
                 SubRegion / Resort, plus the fixture-maintenance commands
                 (dump_resorts_fixture, refresh_eaws_fixtures)
bulletins/       Bulletin ingestion + storage. Owns Bulletin, RegionBulletin,
                 PipelineRun, RegionDayRating, WeatherSnapshot, the ingestion
                 services (slf_fetcher, render_model, day_rating, slf_archive,
                 weather_fetcher), the bulletin and weather ingestion commands
                 (see docs/management-commands.md), and the admin classes for
                 those models
subscriptions/   Signed-token subscription flow (see docs/subscriptions.md);
                 also owns the custom ``Subscriber`` user model
public/          Public-facing bulletin site
  api.py         Plain JsonResponse endpoints consumed by the map page
  api_urls.py    URL routing for /api/ (namespace: api:)
  debug_views.py Staff-only design-debug pages (mounted at /debug/* when DEBUG=True)
templates/       Project-level templates shared across apps
  includes/      Reusable partials (bulletin_header.html, nav.html, …)
src/             Tailwind CSS source (main.css — not served directly)
static/          CSS/JS assets (includes compiled output.css)
logs/            Log files (gitignored except .gitkeep)
```

The `bulletins/` ↔ `regions/` split is deliberate: `regions/` holds stable
shared lookup data (regions, resorts); `bulletins/` holds everything that
originates from the SLF API and the denormalisation that drives the calendar.
`core/` exists so neither app needs to import abstract bases from the other.

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

Use **Poetry** (`poetry add`, `poetry add --group dev`, `poetry update`).
`pyproject.toml` is the single source of truth; there is no `requirements.txt`.

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

## Conventions

### Code

- **Header comment block** on every module describing its purpose;
  **docstring** on every function and class.
- All function arguments are typed, except `*args` and `**kwargs`.
- `ruff` for linting and formatting (includes import sorting); `pre-commit`
  hooks enforce on commit. No `# noqa` without a good reason and a comment
  explaining why.
- British English spellings (colour, behaviour, organise) — except third-party
  identifiers.
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

### Models

Every concrete model ships the full kit — uniformity across models is the
point, so don't skip pieces for "simple" models:

- inherits from the `BaseModel` abstract model;
- an explicit admin class;
- an explicit `to_string()` method (`__str__` delegates to it);
- an explicit `Meta.ordering` (`-created_at` by default);
- a custom queryset;
- a test factory and test coverage.

### Testing

- pytest + FactoryBoy. Tests live in a top-level `tests/` directory that
  mirrors the source tree; each module has a corresponding
  `test_{module_name}.py`.
- All new code must have covering tests; the coverage target is 90%.
- Always run tests via `poetry run tox -e test` (not a bare `pytest` call) —
  the tox env mirrors CI.
- All datetime objects must have `tzinfo`.
- Always call factories with `.create()` (e.g. `RegionFactory.create(...)`) —
  never use direct instantiation (`RegionFactory(...)`). The `.create()`
  classmethod is properly typed and lets mypy infer the correct model
  return type.

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
Existing commands that pre-date them are being migrated; don't copy their
old shape when adding new ones.

1. **Runs with no arguments** — sensible defaults derived from context
   (current date, settings). Required positional arguments are a smell.
2. **Read-only by default** — the bare invocation must never write to the
   database, send mail, or call a paid/rate-limited external service.
   Writing requires an explicit opt-in: new commands use `--commit`
   (preferred); legacy `--dry-run` commands must prompt before writing and
   accept `--no-input` for unattended runs. Don't mix the two shapes in
   one command.
3. **Respect `--verbosity`** in log calls (free via `BaseCommand`).
4. **Exit non-zero on failure** — including a partially failed batch
   (`records_failed > 0`) — so cron/CI can detect it.

Full contract (the two safe command shapes, confirmation rules) plus the
command catalogue and flag reference:
[`docs/management-commands.md`](docs/management-commands.md).

## Frontend

**Tailwind CSS v4** compiled via the `@tailwindcss/cli` package.

- Source: `src/css/main.css` — contains `@import "tailwindcss"`, `@theme` design
  tokens, and component exceptions. Lives outside `static/` so WhiteNoise never
  tries to post-process it.
- Output: `static/css/output.css` — gitignored build artifact loaded by templates.
- All styling uses Tailwind utility classes in templates. Only add custom CSS to
  `src/css/main.css` for things Tailwind cannot express (generated content,
  data-attribute selectors, raw HTML resets).
- Build with the watch command under "Running locally"; production builds add
  `--minify` instead of `--watch`.

**HTMX** patterns:
- Full-page views return a complete HTML response.
- Partial/fragment views return only the inner HTML snippet; they are routed under
  `public/urls.py` with a `partials/` prefix and guarded by `require_htmx`.
- Use `hx-target`, `hx-swap="innerHTML"`, and `hx-indicator` for all dynamic
  requests.

## Design system

The canonical reference is the staff-only **component library at `/_components/`**
(source: [`public/design_tokens.py`](public/design_tokens.py), variant fixtures in
[`public/_component_fixtures.py`](public/_component_fixtures.py)). Read it before
adding any new visual surface. Rules for any change that adds or touches
templates — enforced by `bin/ds-lint` (`tox -e ds-lint`), which blocks every PR
that introduces a violation:

1. **Reuse first, extract second, inline never.** Use an existing partial
   (`_card`, `_button`, `_status_page`, `_collapsible_panel`, `_eyebrow`, …)
   if there is one. If the same shape already exists inline in another
   template, extract a new partial *with a registry entry* — don't add a
   third copy.
2. **Design tokens, not raw Tailwind palette utilities.** Colours: `bg-card`,
   `text-text-1/2/3`, `border-border`, `bg-status-*` — never `bg-slate-200`,
   `text-red-600`. Radius: `rounded-card`/`-tag`/`-pill`/`-sm` — never
   `rounded-[12px]`. Primary CTAs use `templates/includes/_button.html`, not
   inline class strings.
3. **Hex colours belong in `src/css/main.css` `@theme`.** The only legitimate
   template-side hex values are SVG `fill`/`stroke` attributes and the PWA
   `theme-color` meta tag (which can't resolve CSS variables).

Per-line escape hatch, only when a token genuinely can't express the
constraint. The reason is required and audit-visible
(`bin/ds-lint --show-allows`) — write it for a reviewer judging it cold:

```html
{# ds-lint-allow: <reason> #}
<element class="rounded-[16px]">…</element>
```

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
poetry run tox -e ds-lint         # design-system template linter (see "Design system" above)
poetry run tox -e audit           # pip-audit on the locked dependency set
poetry run tox -e sast            # semgrep (Django + Python + security-audit rulesets)
poetry run tox --recreate         # rebuild envs from scratch after a deps change
```

Template formatting is enforced by `djangofmt`, which runs as a pre-commit
hook. Always run `pre-commit run djangofmt --files <path>` after editing
templates so the hook doesn't reformat on commit.

**Before opening a PR**, run `poetry run tox` and fix every failure. For any
change touching a public page, also run `npm run lh` (see
[`docs/lighthouse.md`](docs/lighthouse.md)).

## Cleanup tooling

Two scripts under `bin/` keep local git state in sync with origin. Both
must be run from the primary worktree (on `main`); both refuse to run
from inside a worktree they might delete.

- [`bin/cleanup-merged-branch <branch>`](bin/cleanup-merged-branch) —
  post-merge cleanup of one branch: removes its worktree, deletes the local
  branch, prunes remote tracking refs.
- [`bin/sync-with-origin`](bin/sync-with-origin) — bulk cleanup of every
  redundant worktree and branch (upstream `: gone` or fully reachable from
  `origin/main`). Dry-run by default; `--commit` to delete.

## Linear workflow

Linear (team prefix `SNOW-`) is the issue source of truth. Chat creates and
scopes tickets through `Ready for dev`; Code moves the ticket to
`In Progress` via the Linear MCP immediately after creating the local branch
(no push at that point). The GitHub–Linear integration handles `In Review`
(PR opened) and `Done` (PR merged); both require `SNOW-xxx` in the branch
name or PR body.

- Branch: `feature/SNOW-xxx-short-description` (`fix/SNOW-xxx-…` for bugs,
  `chore/SNOW-xxx-…` for tooling/infra). One ticket per branch.
- Commit subject prefix `SNOW-xxx:` — keeps the ticket reference in the git
  log after squash-merge.
- PR title: `SNOW-42: short imperative summary`. The body must start with
  `Closes SNOW-42` — that magic comment closes the Linear ticket on merge.
- **Stop and ask** if: the scoping comment is missing (scope in Chat first);
  tests fail and the fix isn't obvious; or implementation reveals the scope
  was wrong (comment on the Linear issue first).

Full lifecycle, entry points, scoping-comment contract, and PR body template:
[`docs/linear-workflow.md`](docs/linear-workflow.md).

## Invariants

These must hold at all times. The QA agent and security-auditor check for
drift against this list on every PR.

1. **No `mark_safe()` on user-supplied content** — never bypass Django's
   auto-escaping for data that originates outside the codebase.
2. **Email addresses normalised to lowercase** before storage and lookup —
   `email = email.lower()` at every entry point.
3. **Subscription emails are always async** — never block the request cycle
   with a synchronous SMTP send. Use `django_tasks.task` + `.enqueue()` to
   dispatch email work off the request. The `ImmediateBackend` (dev/test) runs
   tasks inline; `DatabaseBackend` (production) persists and dispatches via
   `db_worker`. Direct `send_mail()` calls outside a `@task` worker are
   prohibited on the hot request path.
4. **HTMX partial views guarded by `require_htmx`** — every fragment endpoint
   must reject plain HTTP requests with a 400.
5. **No secrets in source** — all credentials via `python-decouple`; `.env`
   is gitignored and never committed.

## Feature-specific reference

Read these when working in the relevant area:

| Area | Doc |
|------|-----|
| User personas and core journeys | [`docs/user-journeys.md`](docs/user-journeys.md) |
| Subscriptions (signed tokens, rate limits, email) | [`docs/subscriptions.md`](docs/subscriptions.md) |
| Render model (shape, versioning, day character) | [`docs/render-model.md`](docs/render-model.md) |
| Weather-driven bulletin header (WMO buckets, is_day projection) | [`docs/weather-header.md`](docs/weather-header.md) |
| Map page and JSON API | [`docs/map-and-api.md`](docs/map-and-api.md) |
| Compressed-views peak rating rule (choropleth, tooltip, calendar) | [`docs/compressed-views-rating-rule.md`](docs/compressed-views-rating-rule.md) |
| PWA shell (service worker, manifest icons, cache strategy) | [`docs/offline-map.md`](docs/offline-map.md) |
| Calendar and RegionDayRating | [`docs/calendar.md`](docs/calendar.md) |
| Internationalisation | [`docs/i18n.md`](docs/i18n.md) |
| Lighthouse CI (budgets, perf settings) | [`docs/lighthouse.md`](docs/lighthouse.md) |
| Query-count monitoring (SNOW-13) | [`docs/query-counts.md`](docs/query-counts.md) |
| Management commands (design rules, catalogue, scheduled jobs) | [`docs/management-commands.md`](docs/management-commands.md) |
| Nav partial implementation spec | [`docs/nav_implementation_spec.md`](docs/nav_implementation_spec.md) |
| Feature flags (django-waffle) | [`docs/feature-flags.md`](docs/feature-flags.md) |
| Code review cycles | [`docs/code-reviews/README.md`](docs/code-reviews/README.md) |
| Async operations (background threads, failure modes) | [`docs/async-operations.md`](docs/async-operations.md) |
| Web Push (VAPID keypair, Render wiring, smoke test) | [`docs/push-notifications.md`](docs/push-notifications.md) |
