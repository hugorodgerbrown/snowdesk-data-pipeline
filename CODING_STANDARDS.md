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
core/            Shared abstractions (BaseModel; abstract, no concrete tables),
                 plus HTTP-layer middleware, the require_htmx decorator, and
                 the monitor_query_counts command
regions/         Geographic reference data — MicroRegion / MajorRegion /
                 SubRegion / Resort, plus the fixture-maintenance commands
                 (dump_resorts_fixture, refresh_eaws_fixtures,
                 build_france_fixture, build_switzerland_fixture,
                 audit_resort_regions)
bulletins/       Bulletin ingestion + storage. Owns Bulletin, RegionBulletin,
                 PipelineRun, RegionDayRating, WeatherSnapshot, the ingestion
                 services (data_fetcher / render_model / day_rating /
                 slf_archive / openmeteo_archive / weather_fetcher /
                 weather_display), the dev-only SLF / Open-Meteo mirror
                 endpoints, and the bulletin and weather management commands
subscriptions/   Signed-token email subscription flow — Subscriber, Subscription
public/          Public-facing bulletin site (HTMX-driven). Owns the JSON API
                 used by the map page (api.py / api_urls.py)
tests/           Mirrors the layout of the modules under test
  factories.py   FactoryBoy factories for every model
  fixtures/      Sample CAAML bulletin payloads consumed by pytest
reference_data/  EAWS + MF reference geometry, the EAWS OpenAPI schema, and the CAAML schema doc
src/             Tailwind CSS source (main.css — not served directly)
static/          CSS/JS assets (includes compiled output.css)
logs/            Runtime log files (gitignored except .gitkeep)
```

The `bulletins/` ↔ `regions/` split is deliberate: `regions/` holds
stable shared lookup data (regions, resorts); `bulletins/` holds
everything that originates from the SLF API and the denormalisation that
drives the calendar. `core/` exists so neither app needs to import
abstract bases from the other.

Tests live in a **top-level** `tests/` directory, not inside each app.
The tree under `tests/` mirrors the source tree: `bulletins/models.py`
has tests at `tests/bulletins/test_weather_snapshot_model.py` and
`regions/models.py` has tests at `tests/regions/models/test_models.py`.

---

## 2. Python style

Follow the Zen of Python (h/t Tim Peters):

* Beautiful is better than ugly.
* Explicit is better than implicit.
* Simple is better than complex.
* Complex is better than complicated.
* Flat is better than nested.
* Sparse is better than dense.
* Readability counts.
* Special cases aren't special enough to break the rules.
* Although practicality beats purity.
* Errors should never pass silently.
* Unless explicitly silenced.
* In the face of ambiguity, refuse the temptation to guess.
* There should be one-- and preferably only one --obvious way to do it.
* Although that way may not be obvious at first unless you're Dutch.
* Now is better than never.
* Although never is often better than *right* now.
* If the implementation is hard to explain, it's a bad idea.
* If the implementation is easy to explain, it may be a good idea.
* Namespaces are one honking great idea -- let's do more of those!

### 2.1 Module headers

Every module starts with a docstring block whose first line names the
file and gives a one-line purpose, followed by a short description of
what the module contains and why. See
[bulletins/models.py](bulletins/models.py) or
[bulletins/services/data_fetcher.py](bulletins/services/data_fetcher.py)
for the canonical shape:

```python
"""
bulletins/services/data_fetcher.py — Fetching and persisting SLF bulletins.

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
  forward types (see [bulletins/models.py](bulletins/models.py),
  [regions/models.py](regions/models.py),
  [bulletins/schema.py](bulletins/schema.py),
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
  Ruff auto-detects first-party packages; no `known-first-party` config
  is needed.

### 2.5 Logging

- Every module that logs uses `logger = logging.getLogger(__name__)` at
  module level.
- Use `%s`-style lazy formatting, not f-strings, in log calls:
  `logger.info("Pipeline run %s started", run.pk)`.
- Use `logger.exception(...)` inside `except` blocks to capture
  tracebacks; `logger.error("...", exc_info=True)` is also acceptable.
- The `core`, `regions`, `bulletins`, and `subscriptions` loggers are
  configured in [config/settings/base.py](config/settings/base.py) to
  write to `logs/pipeline.log` with rotation (the filename is legacy and
  intentionally preserved), and errors additionally to `logs/errors.log`.
  Don't reconfigure handlers inside app code.
- `print()` is a lint error (`T2` rule). Use the logger.
- log API responses with `logger.debug()`

### 2.6 Formatting and linting

- `ruff format` is the formatter (black-compatible). Line length is 88.
- `ruff check` runs `A`, `C9`, `D`, `E`, `F`, `I`, `S`, `T2`, `W`.
- Max cyclomatic complexity is 8 (`max-complexity = 8`). If you hit
  `C901`, **refactor** — don't `# noqa` it. The extracted-helper pattern
  in `_process_bulletin` / `run_pipeline` in
  [bulletins/services/data_fetcher.py](bulletins/services/data_fetcher.py)
  is the reference example.
- Only suppress lint warnings with `# noqa: <code>` when there is a good
  reason, and always leave an inline comment explaining why. See the
  `mark_safe` calls in
  [public/templatetags/snowdesk_html.py](public/templatetags/snowdesk_html.py)
  and
  [public/templatetags/card_tags.py](public/templatetags/card_tags.py)
  for acceptable usage.

### 2.7 Datetimes

- **Every** datetime must carry `tzinfo`. `USE_TZ = True` is set in
  settings; naive datetimes will raise warnings in tests.
- Use `datetime.UTC` (Python 3.11+), not `timezone.utc` or `pytz`.
- CAAML timestamps are parsed through `_parse_dt` in
  [bulletins/services/data_fetcher.py](bulletins/services/data_fetcher.py)
  which always returns a UTC-aware datetime.
- In factories, use `tzinfo=UTC` — see
  [tests/factories.py](tests/factories.py).

---

## 3. Django conventions

### 3.1 Models

Every concrete model must:

1. **Inherit from `BaseModel`**
   ([core/models.py](core/models.py)), which provides `id`
   (BigAutoField), `uuid`, `created_at`, `updated_at`.
2. **Define a `Meta`** that inherits from `BaseModel.Meta` and sets an
   explicit `ordering` (default is `-created_at` via BaseModel).
3. **Define `__str__`** returning a human-readable representation.
4. **Define a custom QuerySet** even if empty (`pass`), and expose it
   via `objects = XxxQuerySet.as_manager()`. Domain query methods live
   on the queryset — not on the model.
5. **Have an AdminModel** registered in the owning app's `admin.py`
   ([bulletins/admin.py](bulletins/admin.py),
   [regions/admin.py](regions/admin.py),
   [subscriptions/admin.py](subscriptions/admin.py)) with at minimum
   `list_display`, `search_fields` where useful, and `readonly_fields`
   for timestamp columns.
6. **Have a Factory** in [tests/factories.py](tests/factories.py).
7. **Have test coverage** under `tests/<app>/` mirroring the source
   path (e.g. `tests/regions/models/test_models.py`,
   `tests/bulletins/test_weather_snapshot_model.py`).

Keep business logic **out** of models. Put it in the owning app's
`services/` subdirectory (e.g. [bulletins/services/](bulletins/services/)).
Models may expose thin accessors (e.g. `Bulletin.get_danger_ratings()`
which builds dataclass views over `raw_data`) but must not perform I/O,
fetch, or mutate other records.

### 3.2 TextChoices

- Defined inside the owning model as a nested class when the choice is
  specific to that model (e.g. `PipelineRun.Status`).
- Defined in [bulletins/schema.py](bulletins/schema.py) when the choice
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
  routed under a `partials/` prefix in the owning app's `urls.py`
  (e.g. [public/urls.py](public/urls.py),
  [subscriptions/urls.py](subscriptions/urls.py)) and guarded with the
  `require_htmx` decorator from
  [core/decorators.py](core/decorators.py).
- Views are thin: parse query params, enforce permissions, call the ORM or
  service layer, render a template. No business logic.
- Use `@require_GET` / `@require_POST` from
  `django.views.decorators.http`.
- Type annotations: `def view(request: HttpRequest) -> HttpResponse:`.

### 3.5 Templates

-  No database lookups in templates / templatetags

### 3.6 Services

- Located in each app's `services/` subdirectory (e.g.
  [bulletins/services/](bulletins/services/)). The bulletin ingestion,
  render-model, day-rating, weather-fetching, weather-display, and
  SLF/Open-Meteo archive services all live under `bulletins/services/`.
- Prefer plain functions over classes — composition over inheritance.
- Pass collaborators as arguments rather than reaching for globals or
  building deep class hierarchies.
- Functions should be testable in isolation; integration with Django's
  ORM is fine, but HTTP calls must go through `requests` so tests can
  patch them.
- Keep a module-level logger.

### 3.7 Management commands

Every command under an app's `management/commands/` subdirectory
(`bulletins/management/commands/`, `regions/management/commands/`,
`core/management/commands/`) must:

- Have a module header docstring and class docstring.
- Override `add_arguments(self, parser: ArgumentParser) -> None` with
  fully-typed arguments.
- Override `handle(self, *args: Any, **options: Any) -> None`.
- Be runnable with no arguments and produce no destructive side effects
  by default. Use the explicit `--commit` opt-in for writes (see
  CLAUDE.md → **Management command design**).
- Log start, success, and failure via the module logger in addition to
  using `self.stdout.write(self.style.*)` for operator feedback.
- Raise `CommandError` on fatal failure.

See
[bulletins/management/commands/fetch_bulletins.py](bulletins/management/commands/fetch_bulletins.py)
for the reference shape, and
[docs/management-commands.md](docs/management-commands.md) for the full
catalogue and flag reference.

### 3.8 Settings

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

- **Tailwind CSS v4** compiled via the `@tailwindcss/cli` package.
  - Source file: `src/css/main.css` — contains `@import "tailwindcss"`,
    `@theme` design tokens, and component exceptions (EAWS tints, prose
    resets, `<details>` chevrons).
  - Compiled output: `static/css/output.css` — a gitignored build artifact
    that templates load. Run the CLI to regenerate:
    ```bash
    npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css          # one-off
    npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch   # dev
    npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --minify  # prod
    ```
  - Prefer Tailwind utility classes in templates. Only add custom CSS to
    `main.css` for things Tailwind cannot express (generated content,
    data-attribute selectors, raw HTML resets).
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
  `Model(...)` directly in tests. All models must have a factory.
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
  [tests/regions/models/test_models.py](tests/regions/models/test_models.py)
  and
  [tests/bulletins/services/test_data_fetcher.py](tests/bulletins/services/test_data_fetcher.py)
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
  coverage across the project apps.
- `pytest --cov=core --cov=bulletins --cov=regions --cov=public
  --cov=subscriptions` runs by default via `addopts` in
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
- `gitleaks` for committed secrets
- Local `djangofmt` hook via `.venv/bin/djangofmt`
- Local `mypy` hook via `.venv/bin/mypy core/ bulletins/ regions/
  public/ subscriptions/ tests/ config/` (kept in sync with the
  `tox -e mypy` target)

Install with `poetry run pre-commit install`. Do not bypass hooks with
`--no-verify` — if a hook fails, fix the underlying issue and create a
**new** commit (never amend, as the original commit didn't land).

### 6.3 tox

[tox.ini](tox.ini) defines seven environments. The five default envs
(`fmt`, `lint`, `mypy`, `django-checks`, `test`) run in CI on every
push. `audit` and `sast` are also wired up as tox envs; run them
locally before opening a PR that touches dependencies or security-
sensitive code.

| env              | purpose                                                                                  |
| ---------------- | ---------------------------------------------------------------------------------------- |
| `fmt`            | `ruff format --check .`                                                                  |
| `lint`           | `ruff check .`                                                                           |
| `mypy`           | `mypy core/ bulletins/ regions/ public/ subscriptions/ tests/ config/`                   |
| `django-checks`  | `manage.py check` + `makemigrations --check`                                             |
| `test`           | `pytest --cov=core --cov=bulletins --cov=regions --cov=public --cov=subscriptions tests/` |
| `audit`          | `pip-audit` against the Poetry-exported requirements                                     |
| `sast`           | `semgrep` with the Django + Python + security-audit rulesets                             |

Run the default suite locally with `tox` before pushing.

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
[bulletins/models.py](bulletins/models.py) and
[bulletins/schema.py](bulletins/schema.py).

### 7.2 Dataclass views over JSON

Structured slices of `raw_data` are exposed via frozen dataclasses
(`Elevation`, `DangerRating`, `AvalancheProblem`) defined in
[bulletins/schema.py](bulletins/schema.py). They map CAAML's camelCase
keys to snake_case attributes via `from_dict` classmethods. They are
**read-only views** — they do not validate input, and absent fields
become `None` or empty tuples.

### 7.3 Upserts

Bulletin writes go through `upsert_bulletin` in
[bulletins/services/data_fetcher.py](bulletins/services/data_fetcher.py),
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
