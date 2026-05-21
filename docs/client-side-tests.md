# Client-side test harness

Snowdesk uses [Playwright](https://playwright.dev/) with
[pytest-playwright](https://playwright.dev/python/docs/library) for
end-to-end browser tests that execute inline JavaScript embedded in Django
templates.  The harness was introduced in SNOW-223 to catch the class of bugs
that bit SNOW-217 (share button): template-tag-in-JS-comment parse errors,
script-before-DOM timing failures, and silent clipboard fallback omissions.

---

## What the harness covers

One smoke test (`tests/e2e/test_share_button.py`) exercises the full
share-button flow on the canonical bulletin page:

1. Navigates to `/ch-4115/martigny-verbier/2026-04-08/` (pre-seeded via
   `test_data` fixture).
2. Asserts no JS `pageerror` fires on load (catches script-parse errors).
3. Clicks `[data-bulletin-share-button]` and asserts a POST reaches
   `/api/bulletins/share/` (catches DOM-timing failures).
4. Reads the clipboard and asserts it starts with `http` (catches missing
   clipboard fallback).

**Out of scope** (explicitly excluded from SNOW-223): Firefox/WebKit, visual
regression/screenshot diffing, coverage for other inline scripts (season
sheet, service worker, HTMX swaps, map).

---

## How to run locally

```bash
poetry run tox -e e2e
```

On first run, tox downloads Chromium (~170 MiB) via
`playwright install chromium --with-deps`.  Subsequent runs reuse the
cached binary.

The env is **not** in the default `tox` envlist (it takes 30+ s cold and
is opt-in only).  To run the default suite:

```bash
poetry run tox          # fmt, lint, mypy, django-checks, ds-lint, test
poetry run tox -e e2e   # e2e only
```

---

## How to add a test

1. Create a new file under `tests/e2e/`, e.g.
   `tests/e2e/test_season_sheet.py`.
2. Request the `live_server`, `page`, and `_load_test_data` fixtures (all
   defined in `tests/e2e/conftest.py` or by pytest-playwright).
3. Navigate to the page under test with `page.goto(live_server.url + "/...")`.
4. Assert on page content, network requests, or JS behaviour.

Example skeleton:

```python
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def test_my_feature(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto(f"{live_server.url}/my-page/")
    page.wait_for_load_state("networkidle")
    assert page_errors == [], f"JS errors: {page_errors}"

    # … further assertions …
```

---

## Fixture notes

### `browser_context_args` (in `conftest.py`)

Grants `clipboard-read` and `clipboard-write` permissions to every test
context.  Without this, `navigator.clipboard.writeText()` raises
`NotAllowedError` in headless Chromium (the browser blocks clipboard access
unless the context explicitly grants it via Playwright's permission API).
The override is transparent — tests just use `page.evaluate("() => navigator.clipboard.readText()")` and it works.

### `_load_test_data` (in `conftest.py`)

Function-scoped fixture that runs `call_command("loaddata", "test_data")`
under `django_db_blocker.unblock()`.  It must be function-scoped (not
session-scoped) because pytest-django's `transactional_db` fixture (which
`live_server` implicitly requests) calls `flush` at the start of every test,
wiping any data loaded at session setup time.

---

## CI cadence

The e2e workflow (`.github/workflows/e2e.yml`) runs on every pull request and
every push to `main`, triggered by changes to `**/*.py`, `**/*.html`,
`pyproject.toml`, `poetry.lock`, `tox.ini`, or the workflow file itself.

The Playwright binary cache is keyed on `poetry.lock` so cache hits are
common (the binary version is tied to the `playwright` package).

---

## Known limitations

- **Missing `output.css`**: `static/css/output.css` is gitignored (built by
  the Tailwind CLI).  In CI and in local tox runs where the CSS has not been
  compiled, the browser logs a "Failed to load resource" console error for
  the missing stylesheet.  This is harmless — the test only captures
  `pageerror` events (uncaught JS exceptions), not resource-load console
  messages, so the assertion still passes.
- **Chromium only**: Firefox and WebKit are excluded from scope.  Adding them
  requires extending the `tox -e e2e` command to pass `--browser firefox` etc.
- **`DJANGO_ALLOW_ASYNC_UNSAFE=true`**: The e2e tox env sets this because
  Playwright's internal asyncio event loop is still running when pytest-django
  creates the test database, which triggers Django's `SynchronousOnlyOperation`
  guard.  The live server runs in its own thread, so relaxing the guard is safe
  in this env.
