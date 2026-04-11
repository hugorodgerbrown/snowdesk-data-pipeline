# Coding Standards — Snowdesk Data Pipeline

This document captures the conventions actually in force in this
repository. It is reverse-engineered from the existing source tree and
enforced by `ruff`, `mypy`, `pytest`, and the `pre-commit` hooks wired
up in [.pre-commit-config.yaml](.pre-commit-config.yaml),
[pyproject.toml](pyproject.toml), and [tox.ini](tox.ini).

If a rule below conflicts with something you see in the code, the code
is probably wrong — fix it rather than relaxing the rule.

---

## 1. Repository layout

```
config/          Django project: split settings (base/development/production), urls, wsgi
pipeline/        Core app — ingest, models, services, admin, management commands
  services/      Pure-function modules; no Django request/response coupling
  management/    Django management commands
  templates/     App templates; partials/ holds HTMX fragment responses
  migrations/    Generated — never edit by hand after squashing
public/          Public-facing bulletin site (HTMX-driven)
tests/           Mirrors the layout of the modules under test
  factories.py   FactoryBoy factories for every model
sample_data/     Fixture JSON + CAAML schema documentation
static/          CSS/JS assets
logs/            Runtime log files (gitignored except .gitkeep)
```

Tests live in a **top-level** `tests/` directory, not inside each app.
The tree under `tests/` mirrors the source tree: `pipeline/models.py`
has tests at `tests/pipeline/models/test_models.py`.

---

## 2. Python style

### 2.1 Module headers

Every module starts with a docstring block whose first line names the
file and gives a one-line purpose, followed by a short description of
what the module contains and why. See
[pipeline/models.py](pipeline/models.py) or
[pipeline/services/data_fetcher.py](pipeline/services/data_fetcher.py)
for the canonical shape:

```python
"""
pipeline/services/data_fetcher.py — Fetching and persisting SLF bulletins.

Contains pure-ish functions that:
  1. Fetch a page of bulletins from the SLF CAAML API (fetch_bulletin_page).
  2. Persist a single bulletin into the database (upsert_bulletin).
  3. Orchestrate a full pipeline run across a date range (run_pipeline).

...
"""
```

### 2.2 Docstrings

- **Every** class and function has a docstring. This is enforced by
  ruff's `D` rules and only waived in `__init__.py`, migrations, and
  test modules (see the `per-file-ignores` in
  [pyproject.toml](pyproject.toml)).
- One-line docstrings end with a period.
- Multi-line docstrings use a short imperative summary line, a blank
  line, then details. `Args:` / `Returns:` / `Raises:` sections use
  Google style. A trailing blank line inside the closing `"""` is
  permitted (pydocstyle rules `D406`/`D407`/`D410`–`D417` are disabled).
- Do **not** start the first sentence with "This" (`D404`).
- Section dividers inside large modules use a comment band:
  ```python
  # ---------------------------------------------------------------------------
  # PipelineRun
  # ---------------------------------------------------------------------------
  ```

### 2.3 Type annotations

- All function arguments and return types are annotated. `*args` and
  `**kwargs` are exempt but should still be typed where feasible
  (`*args: Any, **kwargs: Any`).
- Use `from __future__ import annotations` in modules that reference
  forward types (see [pipeline/models.py](pipeline/models.py),
  [pipeline/schema.py](pipeline/schema.py),
  [public/views.py](public/views.py)).
- Use `collections.abc` for `Callable`, `Iterable`, etc. — not `typing`.
- `mypy` runs with `strict_optional`, `warn_return_any`,
  `warn_unused_ignores`, `warn_unreachable`, `disallow_untyped_defs`,
  and `disallow_incomplete_defs`. Don't suppress errors with `# type:
  ignore` unless there's a concrete reason and it's commented.
- For `django-stubs` to resolve model field types, the mypy plugin is
  registered via `[tool.django-stubs] django_settings_module =
  "config.settings.development"`.
- FactoryBoy defeats mypy's type inference through its metaclass. Call
  sites that pass a factory instance to a typed function must
  `cast(Model, ModelFactory())`. See
  [tests/factories.py](tests/factories.py) for the explanation.

### 2.4 Imports

- `ruff` (`I` rule) handles import ordering (isort-compatible).
- `combine-as-imports = true` — group `from x import (a as A, b as B)`.
- `known-first-party` includes `pipeline` and `public`; any new
  top-level package must be added to the isort config in
  [pyproject.toml](pyproject.toml).

### 2.5 Logging

- Every module that logs uses `logger = logging.getLogger(__name__)` at
  module level.
- Use `%s`-style lazy formatting, not f-strings, in log calls:
  `logger.info("Pipeline run %s started", run.pk)`.
- Use `logger.exception(...)` inside `except` blocks to capture
  tracebacks; `logger.error("...", exc_info=True)` is also acceptable.
- The `pipeline` logger is configured in
  [config/settings/base.py](config/settings/base.py) to write to
  `logs/pipeline.log` with rotation. Don't reconfigure handlers inside
  app code.
- `print()` is a lint error (`T2` rule). Use the logger.

### 2.6 Formatting and linting

- `ruff format` is the formatter (black-compatible). Line length is 88.
- `ruff check` runs `A`, `C9`, `D`, `E`, `F`, `I`, `S`, `T2`, `W`.
- Max cyclomatic complexity is 8 (`max-complexity = 8`). If you hit
  `C901`, **refactor** — don't `# noqa` it. The extracted-helper pattern
  in `_process_bulletin` / `run_pipeline` in
  [pipeline/services/data_fetcher.py](pipeline/services/data_fetcher.py)
  is the reference example.
- Only suppress lint warnings with `# noqa: <code>` when there is a good
  reason, and always leave an inline comment explaining why. See the
  `mark_safe` calls in [pipeline/admin.py](pipeline/admin.py) for
  acceptable usage.

### 2.7 Datetimes

- **Every** datetime must carry `tzinfo`. `USE_TZ = True` is set in
  settings; naive datetimes will raise warnings in tests.
- Use `datetime.UTC` (Python 3.11+), not `timezone.utc` or `pytz`.
- CAAML timestamps are parsed through `_parse_dt` in
  [pipeline/services/data_fetcher.py](pipeline/services/data_fetcher.py)
  which always returns a UTC-aware datetime.
- In factories, use `tzinfo=UTC` — see
  [tests/factories.py](tests/factories.py).

---

## 3. Django conventions

### 3.1 Models

Every concrete model must:

1. **Inherit from `BaseModel`**
   ([pipeline/models.py](pipeline/models.py)), which provides `id`
   (BigAutoField), `uuid`, `created_at`, `updated_at`.
2. **Define a `Meta`** that inherits from `BaseModel.Meta` and sets an
   explicit `ordering` (default is `-created_at` via BaseModel).
3. **Define `__str__`** returning a human-readable representation.
4. **Define a custom QuerySet** even if empty (`pass`), and expose it
   via `objects = XxxQuerySet.as_manager()`. Domain query methods live
   on the queryset — not on the model.
5. **Have an AdminModel** registered in
   [pipeline/admin.py](pipeline/admin.py) with at minimum
   `list_display`, `search_fields` where useful, and `readonly_fields`
   for timestamp columns.
6. **Have a Factory** in [tests/factories.py](tests/factories.py).
7. **Have test coverage** in `tests/pipeline/models/test_{module}.py`.

Keep business logic **out** of models. Put it in `pipeline/services/`.
Models may expose thin accessors (e.g. `Bulletin.get_danger_ratings()`
which builds dataclass views over `raw_data`) but must not perform I/O,
fetch, or mutate other records.

### 3.2 TextChoices

- Defined inside the owning model as a nested class when the choice is
  specific to that model (e.g. `PipelineRun.Status`).
- Defined in [pipeline/schema.py](pipeline/schema.py) when the choice
  comes from an external schema (CAAML) and is shared.
- Each member is `NAME = "value", "Human label"`.

### 3.3 Migrations

- Always generated via `python manage.py makemigrations`. Don't
  hand-write unless you know what you're doing.
- `tox -e django-checks` runs `makemigrations --dry-run --check` and
  will fail CI if you change a model without migrating.
- Migrations are excluded from mypy, ruff `D1`, and coverage.

### 3.4 Views

- Full-page views return a complete HTML response.
- HTMX fragment views return only the inner HTML snippet. They are
  routed under a `partials/` prefix (see
  [pipeline/urls.py](pipeline/urls.py)) and guarded with the
  `require_htmx` decorator from [pipeline/views.py](pipeline/views.py).
- Views are thin: parse query params, call the ORM or service layer,
  render a template. No business logic.
- Use `@require_GET` / `@require_POST` from
  `django.views.decorators.http`.
- Type annotations: `def view(request: HttpRequest) -> HttpResponse:`.

### 3.5 Services

- Located in `pipeline/services/`.
- Prefer plain functions over classes — composition over inheritance.
- Pass collaborators as arguments rather than reaching for globals or
  building deep class hierarchies.
- Functions should be testable in isolation; integration with Django's
  ORM is fine, but HTTP calls must go through `requests` so tests can
  patch them.
- Keep a module-level logger.

### 3.6 Management commands

Every command under `pipeline/management/commands/` must:

- Have a module header docstring and class docstring.
- Override `add_arguments(self, parser: ArgumentParser) -> None` with
  fully-typed arguments.
- Override `handle(self, *args: Any, **options: Any) -> None`.
- Support `--dry-run` and `--verbosity` where the operation mutates
  state.
- Log start, success, and failure via the module logger in addition to
  using `self.stdout.write(self.style.*)` for operator feedback.
- Raise `CommandError` on fatal failure.

See
[pipeline/management/commands/fetch_data.py](pipeline/management/commands/fetch_data.py)
for the reference shape.

### 3.7 Settings

- Split across `config/settings/base.py`, `development.py`,
  `production.py`. `DJANGO_SETTINGS_MODULE` is read from the
  environment.
- Secrets and per-env config come from `python-decouple`
  (`config("SECRET_KEY")`). Never hard-code credentials, and never read
  them via `os.environ` directly.
- `base.py` defines the `LOGGING` dict. Don't reconfigure per-env unless
  you must override log levels or handlers.
- `settings/*` modules are excluded from mypy, ruff `F403`/`F405`, and
  pydocstyle rules (star imports are permitted for environment
  overlays).

---

## 4. Frontend

- **Tailwind CSS** via the Play CDN in development; compile with the
  Tailwind CLI for production (see [CLAUDE.md](CLAUDE.md)).
- `static/css/main.css` stays minimal — prefer Tailwind utility classes
  in templates. Only add custom CSS for things Tailwind cannot express.
- **HTMX**:
  - Full-page views return complete HTML; fragment views return only the
    inner snippet.
  - Fragment routes live under a `partials/` prefix and are guarded by
    `@require_htmx`.
  - Use `hx-target`, `hx-swap="innerHTML"`, and `hx-indicator` for all
    dynamic requests.
- Templates live in each app's `templates/<app_name>/` directory.
  Partials go in `templates/<app_name>/partials/`.

---

## 5. Testing

### 5.1 Framework and layout

- **pytest** with **pytest-django** is the runner. `unittest.TestCase`
  subclasses are tolerated but not preferred.
- **FactoryBoy** for test data. Never build model instances with
  `Model(...)` directly in tests.
- Tests live in `tests/`, mirroring the source structure. Each Django
  module `foo/bar.py` has a corresponding `tests/foo/test_bar.py`.
- Per-file ignores for `*tests/*` disable docstring rules, line-length,
  `S101` (asserts), password-related `S1xx` rules, and `S113` so tests
  can use short literal fixtures freely.
- `disallow_untyped_defs` is **off** for `tests.*` — idiomatic pytest
  test functions need not end with `-> None`. Real type errors still
  surface.

### 5.2 Test structure

- Group related tests under a `TestXxx` class with a one-line docstring:
  `"""Tests for Xxx."""`.
- Use descriptive snake_case test method names that read as assertions
  (`test_returns_zero_when_raw_data_empty`).
- Decorate DB-touching classes with `@pytest.mark.django_db`.
- Include a short method docstring describing the invariant under test.
  See
  [tests/pipeline/models/test_models.py](tests/pipeline/models/test_models.py)
  for the reference style.

### 5.3 FactoryBoy

- One factory per model in [tests/factories.py](tests/factories.py).
- Each factory has a nested `Meta` class with a docstring: `"""Factory
  metadata."""`.
- Use `factory.Sequence`, `factory.LazyAttribute`,
  `factory.LazyFunction`, `factory.SubFactory`, and `factory.Faker` for
  generated data.
- Because factory-boy's metaclass breaks mypy's return-type inference,
  any call site passing a factory result to a typed API must wrap it:
  ```python
  from typing import cast
  run = cast(PipelineRun, PipelineRunFactory())
  upsert_bulletin(raw, run)
  ```

### 5.4 Django test client

- `response.url` is not typed on `HttpResponseBase`. Use
  `response["Location"]` for redirect assertions.

### 5.5 Coverage

- Target: **all new code has covering tests**; aim for ≥90% total
  coverage across `pipeline/` and `public/`.
- `pytest --cov=pipeline --cov=public` runs by default via `addopts` in
  [pyproject.toml](pyproject.toml).
- `config/`, `*/migrations/*`, and `__init__.py` are excluded from
  coverage reporting.

---

## 6. Tooling and CI

### 6.1 Dependency management

- **Poetry** is the single source of truth. There is no
  `requirements.txt`.
- The virtualenv lives at `.venv/` inside the repo — pinned via
  [poetry.toml](poetry.toml). This is **by design**, because the
  pre-commit `mypy` hook invokes `.venv/bin/mypy` by repo-relative path
  so it works identically from the CLI and from GUI git clients
  (SublimeMerge, Tower, Fork) which launch git with a minimal
  environment.
- Never change the venv location without also updating
  [.pre-commit-config.yaml](.pre-commit-config.yaml).

### 6.2 Pre-commit

[.pre-commit-config.yaml](.pre-commit-config.yaml) runs:

- `ruff-check --fix`
- `ruff-format`
- `trailing-whitespace`, `end-of-file-fixer`, `check-merge-conflict`,
  `debug-statements`
- Local `mypy` hook via `.venv/bin/mypy pipeline/ public/ tests/
  config/`

Install with `poetry run pre-commit install`. Do not bypass hooks with
`--no-verify` — if a hook fails, fix the underlying issue and create a
**new** commit (never amend, as the original commit didn't land).

### 6.3 tox

[tox.ini](tox.ini) defines five environments, all run in CI:

| env              | purpose                                      |
| ---------------- | -------------------------------------------- |
| `fmt`            | `ruff format --check .`                      |
| `lint`           | `ruff check .`                               |
| `mypy`           | `mypy pipeline/ public/ tests/ config/`      |
| `django-checks`  | `manage.py check` + `makemigrations --check` |
| `test`           | `pytest --cov=pipeline --cov=public --cov=config tests/` |

Run the whole suite locally with `tox` before pushing.

### 6.4 Configuration files

- All ruff, mypy, pytest, coverage, and django-stubs config lives in
  [pyproject.toml](pyproject.toml). There is no `mypy.ini`,
  `.ruff.toml`, or `pytest.ini` — don't create one.
- Poetry settings that must take effect *before* Poetry reads
  `pyproject.toml` (specifically `virtualenvs.in-project`) live in
  [poetry.toml](poetry.toml).

---

## 7. Data conventions

### 7.1 CAAML storage envelope

Raw SLF bulletins are wrapped in a GeoJSON Feature envelope before
storage, so `Bulletin.raw_data` always looks like:

```json
{
  "type": "Feature",
  "geometry": null,
  "properties": { /* full CAAML bulletin */ }
}
```

Read it via `Bulletin._properties` (or the `get_danger_ratings()` /
`get_avalanche_problems()` helpers) — do **not** access
`raw_data["properties"]` directly from callers. See
[pipeline/models.py](pipeline/models.py) and
[pipeline/schema.py](pipeline/schema.py).

### 7.2 Dataclass views over JSON

Structured slices of `raw_data` are exposed via frozen dataclasses
(`Elevation`, `DangerRating`, `AvalancheProblem`) defined in
[pipeline/schema.py](pipeline/schema.py). They map CAAML's camelCase
keys to snake_case attributes via `from_dict` classmethods. They are
**read-only views** — they do not validate input, and absent fields
become `None` or empty tuples.

### 7.3 Upserts

Bulletin writes go through `upsert_bulletin` in
[pipeline/services/data_fetcher.py](pipeline/services/data_fetcher.py),
which uses `Bulletin.objects.update_or_create` keyed on `bulletin_id`.
Re-runs must be idempotent.

---

## 8. Commit and PR hygiene

- **Never** commit without running `pre-commit`. It's installed; use it.
- **Never** skip hooks (`--no-verify`) or bypass signing — fix the
  underlying issue.
- Create **new** commits rather than amending after a hook failure. The
  failed pre-commit never produced a commit; amending would rewrite the
  *previous* commit.
- Don't commit files that may contain secrets (`.env`,
  `credentials.json`, etc.).
- When staging, prefer named files over `git add -A`.
