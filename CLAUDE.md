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
| Offline map (service worker, precache manifest) | [`docs/offline-map.md`](docs/offline-map.md) |
| Calendar and RegionDayRating | [`docs/calendar.md`](docs/calendar.md) |
| Internationalisation | [`docs/i18n.md`](docs/i18n.md) |
| Lighthouse CI (budgets, perf settings) | [`docs/lighthouse.md`](docs/lighthouse.md) |
| Query-count monitoring (SNOW-13) | [`docs/query-counts.md`](docs/query-counts.md) |
| Management command catalogue | [`docs/management-commands.md`](docs/management-commands.md) |
| Nav partial implementation spec | [`docs/nav_implementation_spec.md`](docs/nav_implementation_spec.md) |
