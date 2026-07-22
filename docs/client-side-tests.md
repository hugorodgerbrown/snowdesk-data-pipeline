---
name: client-side-tests
description: Playwright e2e (tox -e e2e) and Vitest JS-unit (tox -e js) harnesses — tests/js/ db.js coverage, when to use which, adding tests
status: current
last-reviewed: 2026-07-22
---

# Client-side test harness

Snowdesk uses [Playwright](https://playwright.dev/) with
[pytest-playwright](https://playwright.dev/python/docs/library) for
end-to-end browser tests that execute inline JavaScript embedded in Django
templates.  The harness was introduced in SNOW-223 to catch the class of bugs
that bit SNOW-217 (share button): template-tag-in-JS-comment parse errors,
script-before-DOM timing failures, and silent clipboard fallback omissions.

SNOW-495 added a second, faster harness — [Vitest](https://vitest.dev/) —
for unit-testing the standalone `static/js/*` PWA modules (db.js, telemetry,
the mutation queue, sw.js cache helpers, …) without a browser. See "JS unit
tests" below for when to reach for which.

---

## JS unit tests (Vitest)

`static/js/*` modules that don't need a real page, DOM event cycle, or
service worker are unit-tested with [Vitest](https://vitest.dev/) under a
jsdom environment, using [fake-indexeddb](https://github.com/dumbmatter/fakeIndexedDB)
for the IndexedDB surface (jsdom itself has no IndexedDB implementation).
This is the fast, headless path — no Chromium download, no live server, no
Django test database.

**When to reach for JS-unit (Vitest) instead of Playwright:**

- The module's behaviour is self-contained — pure logic, storage, or a
  wrapper around a Web API (IndexedDB, `crypto`, `sessionStorage`) — and
  doesn't depend on a rendered Django template, real HTMX swap, or a live
  service worker controlling the page.
- You want to assert on internal state transitions (e.g. `db.js`'s
  `_resetRequired` latch) that would otherwise need a full page load to
  reach.
- The test is inherently about the JS module in isolation, not an
  end-to-end user journey.

**Stay with Playwright (`tox -e e2e`)** when the test is about the SW
lifecycle itself, a real page's DOM/CSS layout, HTMX partial swaps, or any
journey spanning multiple in-page scripts — see "SW-lifecycle tests: real
vs simulated" below.

### Conventions

- Tests live under `tests/js/`, mirroring `static/js/` the way `tests/e2e/`
  mirrors template/view code.
- File naming follows the project's Python-style `test_*.js` convention
  (not Vitest's default `*.test.js`/`*.spec.js` glob) — `vitest.config.mjs`
  overrides `include` to match.
- A frozen browser-IIFE module (e.g. `db.js`, which assigns a
  non-configurable `window.pwaDb`) is loaded by importing it for its side
  effects: `import '../../static/js/db.js';`. Vitest gives each test FILE a
  fresh module graph, so a case that needs a pristine module-level flag
  (like `db.js`'s one-way `_resetRequired` latch) belongs in its own file —
  see `tests/js/test_db_reset_required.js`.
- No coverage configuration — JS coverage isn't a target metric (the 90%
  coverage bar in `CLAUDE.md` is Python-only).

### Running locally

```bash
uv run tox -e js
```

Same opt-in shape as `tox -e e2e` (not in the default `tox` envlist — `npm
ci` is a needless cost on the common local loop). Runs `npm ci && npm run
test:js` (`vitest run`).

### What the JS-unit layer covers (SNOW-495, SNOW-496)

- `db.js` — schema/upgrade, CRUD helpers, `context()`, the sync log, and the
  one-way Reset Required latch (`test_db.js`, `test_db_reset_required.js`).
- `telemetry.js` — `emit()`/`flush()`/`setOptIn()`/`isOptIn()`, critical-event
  `sendBeacon`, opt-out stripping, the opt-in default and operator kill
  switch (each in their own file — see `test_telemetry.js`'s docstring for
  why), the freshness-indicator sample-rate gate, and `db.js`'s
  storage-eviction heuristic (`test_telemetry*.js`).
- `mutation_queue_core.js` / `mutation_queue.js` — backoff/classification,
  enqueue/drain, the nav sync badge, Background Sync feature-detection, the
  failure toast, and the SNOW-462 account-change reconcile/drain-guard
  (`test_mutation_queue*.js`, split across several files for the same
  per-module-instance-state reason as telemetry.js).
- `overlays.js`, `home_intro.js`, `map_help.js` — the shared dismiss
  primitive, and the two overlays' persistence/keyboard/auto-start
  behaviour (`test_overlays.js`, `test_home_intro.js`, `test_map_help.js`;
  the latter drops pixel-positioning assertions jsdom's zero-rect
  `getBoundingClientRect` can't support — none of the ported Playwright
  cases asserted on positions anyway).
- `scrubber_core.js` / `basemap_cache_core.js` — pure math extracted from
  `map.js` (season scrubber/timelapse/ribbon) and `sw.js` (fetch
  classification, basemap cache eviction) behind thin delegators, so logic
  that real MapLibre/service-worker constraints made hard (or impossible)
  to exercise in Playwright gets direct unit coverage
  (`test_scrubber_core.js`, `test_basemap_cache_core.js`).

The e2e suite (`tox -e e2e`) is left holding real-SW/offline journeys
(install, update, kill switch, push, offline map/favourites, the mutation
queue's real-server round trip) and happy-path page flows — see
"SW-lifecycle tests: real vs simulated" below.

---

## What the Playwright suite covers (SNOW-497)

The top layer of the suite is a small, deliberate set of **happy-path user
journeys** — each drives real clicks/fills and asserts only a
user-observable outcome, with **zero `wait_for_timeout`** (auto-waiting
assertions only: `expect(...).to_be_visible()`, `wait_for_url`,
`page.expect_response`):

- `test_public_pages_load.py` — the map homepage, the canonical bulletin,
  and the static/informational pages (`/terms/`, `/help/`, `/privacy/`, …)
  each load with no JS `pageerror`.
- `test_register_subscribe.py` — filling the inline bulletin-page subscribe
  CTA (`#subscribe-cta-<region_id>`) and submitting swaps in the "Check your
  inbox" confirmation card. Email delivery itself is asserted at the pytest
  layer (`tests/accounts/test_email.py` via `mail.outbox`) — a Playwright
  test cannot read a real inbox.
- `test_sign_in_password.py` — the password-toggle reveal, plus a full
  password sign-in (`accounts:sign_in`'s `_password_sign_in` branch) landing
  on `/account/manage/`.
- `test_account_access_link.py` — the magic-link journey: GET renders a
  confirm page with no side effect, POST activates the subscriber and
  redirects to manage.
- `test_sign_out.py` — signing out via the nav's subscriber dropdown POSTs
  `accounts:sign_out`, redirects to sign-in with signed-out nav links
  visible, and a follow-up manage GET redirects back to sign-in.
- `test_account_manage.py` — the authenticated subscriptions dashboard
  renders a `.subscription-card` naming a subscribed region.
- `test_map_bulletin_journey.py` — tapping a region polygon opens the
  MapLibre popup carrying a link to that day's bulletin. The click is
  dispatched via a stubbed `queryRenderedFeatures` + `MAP.fire('click', …)`
  (headless Chromium doesn't reliably composite `regions-fill` for a
  genuine pixel hit-test) — the highest-flake-risk journey in the set; if a
  future change makes even the stubbed click unreliable, prefer falling
  back to the plain selection-state assertion
  `test_map_marker_exclusion.py` already proves over reintroducing a sleep.
- `test_setup_passkey.py` / `test_share_button.py` — WebAuthn registration
  and the share-button flow (SNOW-223's original smoke test — see its own
  docstring for the three regression patterns it guards).

Everything else in `tests/e2e/` is feature-regression / real-SW coverage
(map glyphs, marker exclusion, community reports, the season scrubber, PWA
lifecycle/offline/push) — narrower guards for specific bugs, not journeys,
and out of scope for this rebuild. A few of those retain their own
`wait_for_timeout` calls (deliberately, per their own docstrings) —
`wait_for_timeout` is banned only in the journey modules listed above.

**Out of scope** (unchanged from SNOW-223): Firefox/WebKit, visual
regression/screenshot diffing.

---

## The `real_sw` marker and the xdist split (SNOW-497)

Every module that drives a **real** service worker (`pwa_page` /
`signed_in_page` / `SignedInPage` — see "SW-lifecycle tests: real vs
simulated" below) carries `pytestmark = pytest.mark.real_sw` at module
level: `test_pwa_lifecycle_install/update/offline/kill_and_reset.py`,
`test_pwa_push_journey.py`, `test_offline_map.py`,
`test_offline_basemap_cache.py`, `test_favourites_offline.py`, and
`test_sync_log_panel.py`. The marker is registered in `pyproject.toml`'s
`[tool.pytest.ini_options]`.

`[testenv:e2e]` in `tox.ini` runs two pytest invocations:

```
pytest tests/e2e/ -m "not real_sw" --no-cov -n auto   # SW-free — parallel
pytest tests/e2e/ -m "real_sw"     --no-cov -n0       # real-SW — serial
```

pytest-django gives each xdist worker its own `gwN` test database and
`live_server` binds an OS-assigned free port per worker, so the SW-free set
is safe under `-n auto` — verified empirically (SNOW-497): green under
parallel workers and faster wall-clock than the old single serial run. The
real-SW modules still can't share a worker (each drives its own live SW
registration/IndexedDB sequence against one browser context) and run
second, serially.

Both commands take `{posargs:tests/e2e/}`, so single-file targeting still
works in either direction — `tox -e e2e -- tests/e2e/test_some_file.py`
runs that file against both `-m` filters. Whichever filter doesn't match
anything in that file collects zero tests (pytest exit code 5); a small
`bash -c` wrapper around each command maps that specific "nothing
collected" exit back to 0 so it doesn't fail the env, while a real test
failure (exit 1) still propagates.

---

## How to run locally

```bash
uv run tox -e e2e
```

On first run, tox downloads Chromium (~170 MiB) via
`playwright install chromium --with-deps`.  Subsequent runs reuse the
cached binary.

The env is **not** in the default `tox` envlist (it takes 30+ s cold and
is opt-in only).  To run the default suite:

```bash
uv run tox          # fmt, lint, mypy, django-checks, ds-lint, test
uv run tox -e e2e   # e2e only
```

To target a single file (either SW-free or real-SW):

```bash
uv run tox -e e2e -- tests/e2e/test_map_bulletin_journey.py
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

### SW-lifecycle tests: real vs simulated

Two different fixture families exercise the service worker, and picking
the wrong one for a new test either pollutes an unrelated assertion or
misses the thing you actually meant to test:

- **Simulated** (`_disable_real_sw` in `test_pwa_client_signals.py`, or
  the stripped-`navigator.serviceWorker` pattern used throughout
  `tests/e2e/test_offline_favourite_submit.py` /
  `test_offline_observation_submit.py`) — the real `/sw.js` never
  registers. Use this when the test is about something ELSE that happens
  to load on a page the SW would otherwise control (the install-prompt
  funnel, a real-server mutation-queue round trip) and a real SW's own
  asynchronous lifecycle events would just be timing noise for that
  assertion. (For pure JS-module internals like `db.js`, `telemetry.js`,
  or `mutation_queue.js`, prefer the Vitest harness over a browser
  entirely — `delete navigator.serviceWorker` gets the same isolation for
  free there, since jsdom doesn't define it by default. See the "JS unit
  tests" section above.)
- **Real** (`pwa_page` / `signed_in_page` in `conftest.py`, SNOW-389) — a
  genuine `/sw.js` registers, activates, and controls the page, with
  `wait_for_event()` / `assert_sw_absent()` helpers for the "never stuck,
  adrift, or abandoned" invariant. Use this when the test IS about the SW
  lifecycle itself: install, update, offline, kill switch, or reset — see
  `tests/e2e/test_pwa_lifecycle_*.py` and `test_pwa_push_journey.py`.

Two Playwright/Chromium quirks surfaced while building the real-SW
suite, worth knowing before adding another one:

1. **A service worker's own script fetch is invisible to Playwright.**
   Neither `page.on("request", ...)` nor `page.route()` /
   `context.route()` ever fire for the initial `/sw.js` registration
   fetch or a `registration.update()` re-fetch. To simulate a changed
   `sw.js` (a new deploy), monkeypatch the Django view that serves it
   (`public.views._serve_sw_file`) instead — `live_server` runs in-process,
   so this changes what bytes the live server actually returns.
2. **A second `wait_for_function()` (or any further `page.evaluate()`)
   call, issued while an earlier SW-driven promise is still settling,
   can read back empty** even though the underlying IndexedDB write was
   there moments before. Fold every condition a test needs into ONE
   `wait_for_function()` predicate rather than chaining separate waits —
   see `test_pwa_push_journey.py`'s module docstring for the specific
   case that surfaced this.

Full findings from the spike that shaped this design:
[`tests/e2e/_spike_results.py`](../tests/e2e/_spike_results.py).

---

## Fixture notes

### `browser_context_args` (in `conftest.py`)

Grants `clipboard-read`, `clipboard-write`, and `notifications` permissions
to every test context.  Without the clipboard grants,
`navigator.clipboard.writeText()` raises `NotAllowedError` in headless
Chromium (the browser blocks clipboard access unless the context explicitly
grants it via Playwright's permission API).  The override is transparent —
tests just use `page.evaluate("() => navigator.clipboard.readText()")` and
it works.  The `notifications` grant (SNOW-389) is what lets
`self.registration.showNotification()` resolve inside `sw.js`'s `push`
handler rather than rejecting.

### `pwa_page` / `signed_in_page` (in `conftest.py`, SNOW-389)

Real-service-worker fixtures — see "SW-lifecycle tests: real vs
simulated" above for when to reach for these instead of the
simulated-SW pattern.

Two guarantees (SNOW-427) make `queue:events` rows safe to assert on
from tests using these fixtures:

1. The telemetry buffer **never drains** — an init-script stub answers
   `fetch` calls to `/api/telemetry` with a synthetic 503 (including
   the `pagehide` `keepalive` flush, which `page.route` cannot
   intercept), so `telemetry.js` takes its 5xx branch and leaves rows
   in place. A resolved 503 rather than a rejection because
   `pwa_offline.js`'s own fetch wrapper reveals the offline banner on
   any rejected fetch. `navigator.sendBeacon` is unaffected, so
   client-side `page.route` captures of critical events still work.
2. The `pwa.sw.installed` / `pwa.sw.activated` rows are **already in
   `queue:events`** when the fixture yields — it waits for both before
   its SW-controlled reload, closing the race where the reload tore
   down the SW → page → IndexedDB write chain mid-flight and lost the
   once-per-SW-instance events for good.

### `_load_test_data` (in `conftest.py`)

Function-scoped fixture that seeds the navigable dataset via
`seed_test_dataset()` (`loaddata eaws_CH resorts` + `seed_test_data --all
--commit`) under `django_db_blocker.unblock()`.  It must be function-scoped (not
session-scoped) because pytest-django's `transactional_db` fixture (which
`live_server` implicitly requests) calls `flush` at the start of every test,
wiping any data loaded at session setup time.

---

## CI cadence

The e2e workflow (`.github/workflows/e2e.yml`) runs on every pull request and
every push to `main`, triggered by changes to `**/*.py`, `**/*.html`,
`pyproject.toml`, `uv.lock`, `tox.ini`, or the workflow file itself.

The Playwright binary cache is keyed on `uv.lock` so cache hits are
common (the binary version is tied to the `playwright` package).

---

## Known limitations

- **`output.css` is built before the suite runs**: `static/css/output.css` is
  gitignored (built by the Tailwind CLI), so both the CI e2e workflow
  (`.github/workflows/e2e.yml`) and the `tox -e e2e` env compile it before the
  live server serves statics — CI via a dedicated `npx @tailwindcss/cli` step,
  tox via a `commands_pre` build (SNOW-491).  This matters for
  **layout-dependent** tests: on an unstyled, collapsed page an element can
  report as "outside the viewport" or as intercepting a click, and the failure
  message won't point at the missing stylesheet.  SNOW-491 was exactly this —
  the SNOW-486 z-index tokens (`--z-popup` et al.) were absent from a stale
  `output.css`, so the favourite/report sheets dropped below the map utility
  buttons (`z-index: 4`) and eight tests failed with `<div id="map"> … subtree
  intercepts pointer events`.  The `commands_pre` build makes `tox -e e2e`
  self-sufficient (it needs `npx`/node on the PATH); a fresh Claude worktree
  additionally builds the CSS via `bin/init-worktree` (see
  [`docs/worktrees.md`](worktrees.md)).
- **Chromium only**: Firefox and WebKit are excluded from scope.  Adding them
  requires extending the `tox -e e2e` command to pass `--browser firefox` etc.
- **`DJANGO_ALLOW_ASYNC_UNSAFE=true`**: The e2e tox env sets this because
  Playwright's internal asyncio event loop is still running when pytest-django
  creates the test database, which triggers Django's `SynchronousOnlyOperation`
  guard.  The live server runs in its own thread, so relaxing the guard is safe
  in this env.
