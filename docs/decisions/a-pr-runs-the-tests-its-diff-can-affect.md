---
name: a-pr-runs-the-tests-its-diff-can-affect
description: Why a PR runs pytest-testmon's selection from main's cached map (bin/select-tests, COVERAGE_CORE=ctrace) or the full suite
status: current
last-reviewed: 2026-10-24
---

# A PR runs the tests its diff can affect

**Decision.** Pushes to `main` and `release` run the whole Python suite, with
coverage, exactly as before (SNOW-1221). Beside it on every push to `main`,
the `Record the test map` job runs the suite again with `--testmon`, which
records for every test the project lines that ran during it, and saves
that map (`.testmondata`) to the Actions cache as `testmon-main-<sha>`. A
pull request restores the newest map and asks `bin/select-tests` whether
testmon can be trusted with the diff. If it can, the job runs
`tox -e test -- --testmon --no-cov`, which reruns only the tests whose
recorded lines changed. If it cannot, or no map was restored, the job runs
the full suite. The job is still named `Run tests`, so the required context
reports exactly as before.

`e2e.yml` also skips Playwright on a PR whose only changes are icons
redrawn in place (`static/icons/**`, `static/favicon*.svg`, status
`modified`). An added, removed or renamed icon still runs the journeys,
because the pages and the service worker refer to icons by name.

**Why.** A docstring fix ran all ~10,000 Python tests. Measured locally, the
same docstring change under testmon selected 46 tests and took 18 seconds
against 150 for the suite. Most of a PR's
pushes change a handful of functions, and testmon knows which tests run
them.

testmon only sees Python that runs *inside a test*, so `bin/select-tests`
sends a diff to the full suite when it touches anything else:

* any non-Python file: templates, fixtures, static, data, `pyproject.toml`,
  `uv.lock`, `tox.ini`, workflows;
* `config/`, `manage.py`, any `conftest.py`, any migration;
* a new or deleted module;
* a changed line of Python outside a function body, other than a docstring,
  an import, a comment or a blank line. This covers class attributes (model,
  form and admin fields), decorators, signatures, URL patterns and module
  constants. Django runs all of these once, at import, so testmon credits
  them to whichever test imported the module first, or to no test at all.

Test modules (`test_*.py`) are safe anywhere, because testmon fingerprints a
test's own file as a whole. Docs paths are ignored, as the docs-only gate
already ignores them.

**A stale map is safe.** testmon compares the files it recorded with the
files in the checkout. `bin/select-tests` diffs against the map's own
commit, taken from the restored cache key, and not against the PR's base.
So anything that landed on `main` after the map was recorded is part of
both comparisons, and an old map can only select *more* tests. Three
cases fall back to the full suite: a missing map (first run, or an evicted
cache), a commit the clone cannot resolve, and any error inside the
selector. A PR never saves a map, so one PR's run can never become
another PR's map.

**Three traps, all guarded.**

1. On Python 3.12 and later, coverage defaults to the `sys.monitoring` core,
   which reports a line once and then stops. testmon switches context per
   test, so every test after the first to run a line is recorded as not
   depending on it. Measured here: a change to `upsert_weather` selected 3 of
   the 39 tests that call it. A map built that way silently skips tests.
   Every `--testmon` run therefore sets `COVERAGE_CORE=ctrace`, and
   `tests/conftest.py` refuses to start a testmon run without it.
2. testmon 2.2 raises `IndexError` on a traced file with no extension, which
   is every `bin/` script that `tests/bin/` imports. `tests/conftest.py`
   gives testmon a filename it reads as `.py`. Remove the shim when testmon
   handles extensionless files itself.
3. testmon and pytest-cov in the same process under-count coverage: the
   full suite measured 32% that way against 96% on its own. That would fail
   main's 90% floor, so the map is recorded by its own job with `--no-cov`,
   and main's `Run tests` is left exactly as it was. The map job is not a
   required check. If it fails or is cancelled, PRs use the previous map.

**Consequences.**

* A selective PR run reports no coverage, because a subset cannot meet the
  90% floor. Every full run still enforces it: `main`, `release`, and every
  PR the selector sends to the full suite. A PR that drops coverage under
  90% while passing selection is caught on `main`.
* The selector sees the whole PR against the map, not the latest push. A PR
  that edits a template runs the full suite on every push, including a
  later docstring-only push.
* If a test has an effect testmon cannot trace (a subprocess, a file read at
  import), a selective run can miss it, and the full run on `main` catches
  it after merge. When that happens, add the input to the selector's
  full-suite rules rather than working around it in the test.
* Test the selector and the shims with `tests/bin/test_select_tests.py` and
  `tests/test_testmon_support.py`. Run it locally with
  `uv run python bin/select-tests --since origin/main`.
