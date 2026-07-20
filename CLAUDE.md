# CLAUDE.md — Snowdesk

## Project overview

Django-based data pipeline that fetches avalanche bulletins from three
providers — SLF (Swiss Institute for Snow and Avalanche Research), ALBINA
(EUREGIO avalanche.report), and Météo-France — normalises them to CAAML v6
JSON, stores them, and renders them on a public bulletin site. The frontend
uses HTMX for dynamic updates without a full JavaScript framework.

Domain term → code symbol map: [`docs/glossary.md`](docs/glossary.md).
Accepted architectural decisions (the "why"): [`docs/decisions/`](docs/decisions/).

Python 3.14 / Django 6.0 (pinned in `pyproject.toml`). If tox envs behave
oddly after a dependency change, rebuild them with `uv run tox --recreate`.

## Architecture

```
config/          Django project settings (split base/development/production)
core/            Shared abstractions (BaseModel; abstract, no concrete tables),
                 plus HTTP-layer middleware and the monitor_query_counts command
regions/         Geographic reference data — MicroRegion / MajorRegion /
                 SubRegion / Resort, plus the fixture-maintenance commands
                 (dump_resorts_fixture, refresh_eaws_fixtures)
bulletins/       Everything that originates from provider APIs — the models
                 (Bulletin, RegionBulletin, PipelineRun, RegionDayRating,
                 WeatherSnapshot, …), the per-provider fetchers/translators
                 and render-model services under services/, the ingestion
                 commands (see docs/management-commands.md), and their admin
                 classes
accounts/        Signed-token subscription flow (see docs/accounts.md);
                 owns the ``Subscriber`` profile model (OneToOne to auth.User,
                 not AUTH_USER_MODEL itself) and ``PasskeyCredential`` (FK
                 to auth.User — any authenticated user can register passkeys)
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

The `bulletins/` ↔ `regions/` split is deliberate — rationale in
[`docs/decisions/bulletins-regions-split.md`](docs/decisions/bulletins-regions-split.md).

## Running locally

```bash
cp .env.example .env          # fill in values
uv sync
npm install
uv run python manage.py migrate

# Terminal 1: Tailwind CSS watcher
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch

# Terminal 2: Django dev server
uv run python manage.py runserver

# Terminal 3: local email sink — captures all outbound mail (no real delivery)
mailpit                       # brew install mailpit  (or: docker run -p 1025:1025 -p 8025:8025 axllent/mailpit)
```

Email uses Django's SMTP backend everywhere; dev points it at **Mailpit**
(SMTP on `localhost:1025`, web inbox at <http://localhost:8025>) — the
actively-maintained successor to the abandoned MailHog. Account-access and
subscription flows all deliver here in local dev. Tests never open a socket
to Mailpit — pytest-django's test harness swaps in the in-memory `locmem`
backend, so email assertions run against `mail.outbox`.

In a Claude worktree `bin/init-worktree` runs automatically and seeds
`db.sqlite3` from fixtures + dev users — see [`docs/worktrees.md`](docs/worktrees.md)
for credentials and the force-reseed procedure.

## Dependency management

Use **uv** (`uv add`, `uv add --dev`, `uv lock --upgrade`).
`pyproject.toml` is the single source of truth (PEP 621 `[project]` +
PEP 735 `[dependency-groups]`); there is no `requirements.txt`. The resolved
set is pinned in `uv.lock` — commit it with every dependency change.

The virtualenv lives at `.venv/` inside the repo — this is uv's default and
also **by design**; don't relocate it without reading
[`docs/decisions/in-project-venv.md`](docs/decisions/in-project-venv.md)
(the pre-commit mypy hook depends on the path).

When a runtime dependency is added via `uv add`, **also add it to the
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
- **No Django signals for side effects** — save-time side effects are called
  inline from the relevant service function, never via `post_save`
  ([why](docs/decisions/no-signals-for-side-effects.md)).

#### Known non-issues — do not flag

These are correct as written; reporting them as bugs is a false positive.

- **`except A, B:` (comma-separated, no parentheses) is valid Python 3.14
  syntax** ([PEP 758](https://peps.python.org/pep-0758/)), equivalent to
  `except (A, B):`. This project pins Python 3.14 and uses the form
  deliberately (e.g. `favourites/views.py`, `regions/services/point_match.py`).
  It is **not** Python 2 syntax and **not** a `SyntaxError` — do not report it
  or "fix" it to the parenthesised form. (A `SyntaxError` from this line means
  something invoked the system Python 3.9, not the project interpreter; run
  `uv run python` instead.)

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
- Always run tests via `uv run tox -e test` (not a bare `pytest` call) —
  the tox env mirrors CI.
- All datetime objects must have `tzinfo`.
- Always call factories with `.create()` (e.g. `RegionFactory.create(...)`) —
  never use direct instantiation (`RegionFactory(...)`). The `.create()`
  classmethod is properly typed and lets mypy infer the correct model
  return type.

## Data sources

Three providers, one canonical storage shape (CAAML v6 JSON); all fetched
via the `fetch_bulletins` command.

- **SLF** (`bulletins/services/slf_fetcher.py`) — paginated CAAML list API,
  no auth, no date filter:
  `https://aws.slf.ch/api/bulletin-list/caaml/{lang}/json?limit={n}&offset={n}`.
  Reverse-chronological; the pipeline pages until it passes the start-date
  boundary. Historical depth limits: [`docs/slf-api-history.md`](docs/slf-api-history.md).
- **ALBINA** (`bulletins/services/albina_fetcher.py`) — EUREGIO
  avalanche.report CDN, no auth; per-day CAAML v6 JSON URLs for the AT-07,
  IT-32-BZ, and IT-32-TN regions. 404 means "no bulletin".
- **Météo-France** (`bulletins/services/meteofrance_fetcher.py`,
  `meteofrance_translator.py`) — DPBRA XML per massif behind an API key
  (`METEOFRANCE_API_KEY`), translated to the CAAML v6 shape that
  `upsert_bulletin` expects
  (mapping spec: [`docs/meteofrance-mapping.md`](docs/meteofrance-mapping.md)).

Raw bulletins from all three providers are wrapped in a GeoJSON Feature
envelope before storage — `{ type: "Feature", geometry: null, properties: {…} }`
([why](docs/decisions/geojson-feature-envelope.md)).

**Canonical payload examples** live in `tests/sentinels/` — three graded
cases (A single-level, B structurally-enhanced, C split-day multi-problem)
per provider, each with a README, enforced by a round-trip test. Read a
sentinel before reasoning about any provider's payload shape; don't trust
prose descriptions of the schema.

## Management command design

These rules apply to **every** new or refactored management command
(commands pre-dating them are being migrated — don't copy their old shape):

1. **Runs with no arguments** — the bare invocation does the most useful
   thing for the common case; required positional arguments are a smell.
2. **Never alters data by default** — read-only unless the caller takes an
   explicit step: either an explicit `--commit` flag (preferred for new
   commands) or `--dry-run` + `Proceed? [y/N]` prompt with `--no-input`
   for unattended runs. Never mix the two shapes in one command. Full
   rationale: [`docs/decisions/dry-run-default-commands.md`](docs/decisions/dry-run-default-commands.md).
3. **Respects `--verbosity`** in log calls.
4. **Exits non-zero on failure**, including partially failed batches
   (`records_failed > 0`), so cron/CI can detect it.

Full contract plus the command catalogue and flag reference:
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
templates — enforced by `bin/ds-lint` (`tox -e ds-lint`, and the `lint-guards`
CI workflow), which blocks every PR that introduces a violation:

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
dependencies (independent of the uv-managed venv), so a tox run mirrors what CI
will execute — catching the "works on my machine" class of failure before a
PR is opened.

```bash
uv run tox                    # run every env (fmt, lint, mypy, django-checks, test)
uv run tox -e test            # one env at a time
uv run tox -e mypy
uv run tox -e django-checks
uv run tox -e fmt             # ruff format --check
uv run tox -e lint            # ruff check
uv run tox -e ds-lint         # design-system template linter (see "Design system" above)
uv run tox -e docs-lint       # docs frontmatter + CLAUDE.md routing linter (see "Documentation" below)
uv run tox -e audit           # pip-audit on the locked dependency set
uv run tox -e sast            # semgrep (Django + Python + security-audit rulesets)
uv run tox --recreate         # rebuild envs from scratch after a deps change
```

Template formatting is enforced by `djangofmt`, which runs as a pre-commit
hook. Always run `pre-commit run djangofmt --files <path>` after editing
templates so the hook doesn't reformat on commit.

**Before opening a PR**, run `uv run tox` and fix every failure. For any
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

## Path to live

Deploys are split across two branches (hosted on Render):

- **`main` → Staging** — every merge auto-deploys one web dyno.
- **`release` → Production** — three services (web + scheduler + task
  worker, one shared DB) deploy when `release` is **fast-forwarded** to
  `main` (`release` behaves like a tag that moves with `main`; no merge
  commit, no PR). The ruleset allows the advance only as a fast-forward
  whose target commit's checks are already green.

Fast-forwarding `release` also fires [`release.yml`](.github/workflows/release.yml),
which tags the commit **CalVer** (`YYYY.MM.DD`, `.N` for a second release
the same day) and creates a GitHub Release. The auto-generated notes list
the merged PRs — `SNOW-xx:` titles make the Release the record of which
tickets reached production. Linear `Done` still fires on merge to `main`
(work complete, on staging); production shipment is the GitHub Release.

Staging and production use **separate databases** — `build.sh` migrates on
every deploy, so staging must never point at the production DB. Open a
release with `bin/cut-release` (dry-run by default). Full flow, Render
topology, and one-time setup: [`docs/deployment.md`](docs/deployment.md).

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

## Documentation

Every doc under `docs/` carries YAML frontmatter (`name`, `description`,
`status: current|draft|historical`, `last-reviewed`) and must be reachable
from the routing table below — `bin/docs-lint` (`tox -e docs-lint`, and the
`lint-guards` CI workflow) enforces both. The `description` line is the
retrieval key: front-load the model/command/URL nouns an agent would search
for. `docs/code-reviews/`, `docs/qa/`, and `docs/research/` hold dated
artefacts, not living documentation, and are exempt.

When you make a non-obvious architectural choice, add a file to
[`docs/decisions/`](docs/decisions/) (format in its README). When a domain
term gains a code symbol, add a line to [`docs/glossary.md`](docs/glossary.md).

## Feature-specific reference

Read these when working in the relevant area:

| Area | Doc |
|------|-----|
| Domain term → code symbol map | [`docs/glossary.md`](docs/glossary.md) |
| Accepted architectural decisions | [`docs/decisions/`](docs/decisions/) |
| How to read an avalanche bulletin (domain primer) | [`docs/bulletin-guide.md`](docs/bulletin-guide.md) |
| User personas and core journeys | [`docs/user-journeys.md`](docs/user-journeys.md) |
| Accounts (signed tokens, rate limits, email) | [`docs/accounts.md`](docs/accounts.md) |
| Login/logout manual test checklist | [`docs/auth-testing-checklist.md`](docs/auth-testing-checklist.md) |
| Render model (shape, versioning, day character) | [`docs/render-model.md`](docs/render-model.md) |
| Day character rules (original spec) | [`docs/day_character_rules_spec.md`](docs/day_character_rules_spec.md) |
| Weather-driven bulletin header (WMO buckets, is_day projection) | [`docs/weather-header.md`](docs/weather-header.md) |
| Map page and JSON API | [`docs/map-and-api.md`](docs/map-and-api.md) |
| Compressed-views peak rating rule (choropleth, tooltip, calendar) | [`docs/compressed-views-rating-rule.md`](docs/compressed-views-rating-rule.md) |
| PWA shell (service worker, manifest icons, cache strategy) | [`docs/offline-map.md`](docs/offline-map.md) |
| Offline-first PWA compliance index (spec §12 non-negotiables) | [`docs/offline-first.md`](docs/offline-first.md) |
| Calendar and RegionDayRating | [`docs/calendar.md`](docs/calendar.md) |
| Internationalisation | [`docs/i18n.md`](docs/i18n.md) |
| Lighthouse CI (budgets, perf settings) | [`docs/lighthouse.md`](docs/lighthouse.md) |
| Query-count monitoring (SNOW-13) | [`docs/query-counts.md`](docs/query-counts.md) |
| Management commands (design rules, catalogue, scheduled jobs) | [`docs/management-commands.md`](docs/management-commands.md) |
| Météo-France DPBRA → CAAML field mapping | [`docs/meteofrance-mapping.md`](docs/meteofrance-mapping.md) |
| Météo-France live ingest operations | [`docs/meteofrance-live-ingest.md`](docs/meteofrance-live-ingest.md) |
| SLF API historical-depth probe (2026-05-01) | [`docs/slf-api-history.md`](docs/slf-api-history.md) |
| Archive PDF URL patterns per provider | [`docs/archive_pdfs/`](docs/archive_pdfs/) |
| Nav partial implementation spec | [`docs/nav_implementation_spec.md`](docs/nav_implementation_spec.md) |
| Feature flags (django-waffle) | [`docs/feature-flags.md`](docs/feature-flags.md) |
| Client-side Playwright tests (`tox -e e2e`) | [`docs/client-side-tests.md`](docs/client-side-tests.md) |
| Manual testing scenarios | [`docs/testing-scenarios.md`](docs/testing-scenarios.md) |
| Existing in-house packages to reuse | [`docs/useful-repos.md`](docs/useful-repos.md) |
| Path to live (staging/production branch split, releases) | [`docs/deployment.md`](docs/deployment.md) |
| Linear workflow (full lifecycle) | [`docs/linear-workflow.md`](docs/linear-workflow.md) |
| Code review cycles | [`docs/code-reviews/README.md`](docs/code-reviews/README.md) |
| Async operations (background threads, failure modes) | [`docs/async-operations.md`](docs/async-operations.md) |
| Web Push (VAPID keypair, Render wiring, smoke test) | [`docs/push-notifications.md`](docs/push-notifications.md) |
| Telemetry pipeline (/api/telemetry receiver, event allowlist, pwa.* signals) | [`docs/telemetry-pipeline.md`](docs/telemetry-pipeline.md) |
| MCP server (POST /api/mcp/ JSON-RPC tools, season cost caps, fuzzy region search) | [`docs/mcp-server.md`](docs/mcp-server.md) |
| IndexedDB scaffolding (window.pwaDb, queue:events, meta:app, Reset Required) | [`docs/indexeddb-scaffolding.md`](docs/indexeddb-scaffolding.md) |
| Mutation queue (window.pwaMutationQueue, Idempotency-Key, backoff, Background Sync, sync badge, failure toast) | [`docs/mutation-queue.md`](docs/mutation-queue.md) |
| Reset the live DB after a migration-history rewrite | [`docs/runbooks/reset-live-db.md`](docs/runbooks/reset-live-db.md) |
| Rename subscriptions app to accounts on an existing DB (table rename, InconsistentMigrationHistory) | [`docs/runbooks/rename-subscriptions-to-accounts.md`](docs/runbooks/rename-subscriptions-to-accounts.md) |
| Worktree DB seeding, dev credentials, test_data coverage | [`docs/worktrees.md`](docs/worktrees.md) |
