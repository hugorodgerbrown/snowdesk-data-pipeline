---
name: client-side-tests
description: Which test layer (pytest / Vitest tests/js / Playwright tests/e2e), the 15-test e2e cap and exclusion list, tox -e e2e and tox -e js
status: current
last-reviewed: 2026-08-07
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

## Which layer? Read this before writing a client-side test

Work top-down and **stop at the first layer that can hold the assertion**.
A test in the wrong layer is a defect regardless of what it asserts.

| Layer | Directory | Holds |
|-------|-----------|-------|
| pytest | `tests/` | Anything the Django test client can reach — status codes, redirects, rendered HTML, HTMX fragment responses |
| Vitest | `tests/js/` | Anything a `static/js/` module does that jsdom can observe — logic, arithmetic, storage, state machines, class strings, IndexedDB |
| Playwright | `tests/e2e/` | Only what needs a real browser against a live server — WebGL canvas, service worker, clipboard/WebAuthn, multi-script journeys |

Vitest is the **default** for client-side behaviour. It is fast,
deterministic, and in the default `tox` envlist, so it runs on every local
`uv run tox` rather than waiting for CI.

### The `tests/e2e/` cap

**At most ~15 tests. One file per user journey. Each under 40 lines. Each
mapping to a named scenario family in
[`testing-scenarios.md`](testing-scenarios.md).**

The suite mirrors the manual test script and nothing else. It answers one
question — *can a user still see the map, read a bulletin, search, sign in,
add a favourite, and reload offline?* — and it must fail loudly for a
broken page, not for a shifted pixel.

Adding a sixteenth test means deleting one, or changing this rule
deliberately in a ticket that says so. It is not a soft target: the cap
exists because the suite has twice grown until it was too slow and too
flaky to trust. SNOW-494 cut it to 110 tests on 22 July 2026; by 7 August
it was 280 across 18,309 lines, because every UI ticket bundled an e2e
test and nothing said stop.

### The cap is a lint, not a convention (`tox -e e2e-lint`)

`bin/e2e-lint` enforces three invariants, and blocks any PR that breaks
one:

1. **At most 15 test functions** across the whole suite. The message names
   how many need to go.
2. **No test function over 40 lines.** A long browser test is one
   asserting things a unit test should own.
3. **Every test module declares the scenario family it mirrors**, as a
   `Scenario:` line in its module docstring, and the ID must be a real
   heading in [`testing-scenarios.md`](testing-scenarios.md). This keeps
   the suite honest in both directions — a test with no scenario is not a
   smoke test, and a renamed scenario surfaces as a lint failure rather
   than rotting quietly.

```python
"""tests/e2e/test_map_search.py — search for a region and land on it.

Scenario: MS1, MS2
"""
```

A journey with genuinely no manual scenario opts out with a reason, which
is audit-visible via `bin/e2e-lint --show-scenarios`:

```python
"""Scenario: none — clipboard ceremony, no manual scenario covers it."""
```

Raising `MAX_TESTS` in `bin/e2e-lint` is a deliberate edit in a ticket that
says why. It is not a way to land a PR — the two previous overruns both
happened one reasonable-looking test at a time.

`e2e-lint` runs in the default `tox` envlist and in the `lint-guards` CI
matrix alongside `ds-lint`, `js-globals-lint`, `i18n-lint` and `docs-lint`.
It costs nothing to run — no browser, no live server, pure AST — so there
is no reason to keep it out of a local `uv run tox`.

### Does not belong in `tests/e2e/` — send it down a layer

If a proposed test asserts any of the following, it is a Vitest or pytest
test wearing a browser costume:

- **Numbers** — byte counts, size estimates, budget totals, tile counts,
  cache quotas, eviction order. → `tests/js/`
- **Geometry and layout maths** — bounding boxes, grid cell polygons,
  frame dimensions, zoom-to-ground locks, clamping. → `tests/js/`
- **State machines** — idle → busy → done → error transitions, retry and
  backoff, disabled/enabled control state. → `tests/js/`
- **Class strings and copy** — what CSS classes or text a module writes
  into the DOM. → `tests/js/`
- **Persistence** — what survives a reload, what a rename writes, what a
  removal deletes. → `tests/js/` (fake-indexeddb covers this)
- **Status codes, redirects and 404s** — a missing region needs no
  Chromium. → `tests/`
- **HTMX fragment responses** — assert the response, not the swap, unless
  the swap itself is the journey. → `tests/`

The honest test of whether something belongs here: *would a manual tester
following `testing-scenarios.md` notice this break?* If the answer is no,
it is not a smoke test.

### Its own coverage rule

The project's 90% coverage target is Python-only and does not apply to
`tests/e2e/`. **Do not add an e2e test to raise coverage** — coverage of a
browser journey is not a meaningful number, and chasing it is what
produced the two previous overruns.

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

`js` is **in the default `tox` envlist** (as of SNOW faster-iterative mode,
2026-07-25): Vitest is fast and deterministic, so `uv run tox` runs it
automatically. The one cost is the `npm ci` clean install the env runs first;
CI's dedicated `js.yml` runs the same env as the backstop. This differs from
`tox -e e2e`, which stays **out** of the default envlist (slow + flaky) and
is delegated to CI. Runs `npm ci && npm run test:js` (`vitest run`).

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

## What the Playwright share-button smoke test covers

One smoke test (`tests/e2e/test_share_button.py`) exercises the full
share-button flow on the canonical bulletin page:

1. Navigates to `/ch-4115/martigny-verbier/2026-04-08/` (pre-seeded via
   the `_load_test_data` fixture).
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

---

## How to add a test

**Almost always, the answer is: don't — add it to `tests/js/` instead.**
Read "Which layer?" above first. The suite is capped, so a new e2e test is
a deliberate act, not a routine one.

0. **Clear the gate.** Answer all four in the PR description, or write the
   test somewhere else:
   - Which scenario family in [`testing-scenarios.md`](testing-scenarios.md)
     does this cover? (No answer → not a smoke test.)
   - Why can neither pytest nor Vitest hold it?
   - Which existing e2e test is being deleted to make room, if the suite
     is at its cap?
   - Is it under 40 lines?
1. Create a new file under `tests/e2e/`, named for the **journey**, not the
   feature — `test_sign_in.py`, not `test_passkey_button_disabled_state.py`.
2. Request the `live_server`, `page`, and `_load_test_data` fixtures (all
   defined in `tests/e2e/conftest.py` or by pytest-playwright).
3. Navigate to the page under test with `page.goto(live_server.url + "/...")`.
4. Assert the journey completed, and assert `page_errors == []`. Resist
   asserting anything else — every extra assertion is a future flake and a
   reason someone will delete the whole file instead of fixing it.

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
   (`apps.public.views._serve_sw_file`) instead — `live_server` runs in-process,
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

`tox -e e2e` also runs with `--reruns 2 --reruns-delay 1`
(pytest-rerunfailures, `e2e` dependency group only) — a bounded automatic
retry net for the real-SW/CacheStorage timing flakes that can't be made
fully deterministic (SNOW-516). The unit `test` env deliberately has no
rerun behaviour, so its determinism is preserved.

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
