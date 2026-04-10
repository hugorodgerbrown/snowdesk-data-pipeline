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
static/          CSS and JS assets (HTMX loaded via CDN)
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

## Running locally

```bash
cp .env.example .env          # fill in values
poetry install
poetry run python manage.py migrate
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
```

## Frontend

**Tailwind CSS** via the Play CDN (`@tailwindcss/browser@4`) in development.
For production, compile with the Tailwind CLI:
```bash
npx @tailwindcss/cli -i ./static/css/main.css -o ./static/css/output.css --minify
```
`static/css/main.css` is intentionally minimal — all styling is done with utility
classes in templates. Only add custom CSS there for things Tailwind cannot express.

**HTMX** patterns:
- Full-page views return a complete HTML response.
- Partial/fragment views return only the inner HTML snippet; they are routed under
  `pipeline/urls.py` with a `partials/` prefix and guarded by `require_htmx`.
- Use `hx-target`, `hx-swap="innerHTML"`, and `hx-indicator` for all dynamic
  requests.

## Code style

- `black` for formatting, `ruff` for linting, `isort` for imports.
- `pre-commit` hooks enforce these on commit.
- Do not suppress linting warnings with `# noqa` unless there is a good reason,
  and always leave a comment explaining why.
- Ensure that all function arguments are typed, except *args and **kwargs

## Django coding rules

- All models to inherit from `BaseModel` abstract model
- All models to have an explicit AdminModel
- All models to have an explicit `to_string()` method
- All models to have an explicit test Factory representation
- All modesl to have test coverage (see Testing section)
- All models to have an explicit `order_by` (`created_at` by default)
- All models to have a custom queryset

### Testing

- Tests to use pytest
- Tests to use FactoryBoy
- Tests in a top level directory called "tests" that then mirrors the strucuture of the source files it's testing. Each Django module should have a
corresponding test_{module_name}.py that contains the tests.
- All new code must have covering tests
- Always run tests after code changes and ensure 100% pass rate and 90% coverage.
- Use tox to run local CI and tests
