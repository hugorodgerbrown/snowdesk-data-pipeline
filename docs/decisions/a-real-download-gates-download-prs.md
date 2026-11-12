---
name: a-real-download-gates-download-prs
description: Why tests/offline (tox -e offline, real downloads in Chromium) gates download-path PRs; fixed Martigny-Verbier test
status: current
last-reviewed: 2026-11-12
---

# A real download gates every PR that touches downloads

**Decision.** `.github/workflows/offline-assurance.yml` runs `tox -e offline`
on every pull request whose diff can change what a download does:
`static/js/*.js`, `apps/map/`, the service-worker shell, the map views and
APIs, `config/settings/base.py`, `tests/offline/` and the lockfiles. Its
`Offline maps assurance` check is required. Any other PR skips the job,
which still reports a skipped check and satisfies the ruleset. The Monday
cron stays and keeps walking the fuzzed space on `main`.

`tests/offline/test_martigny_verbier_downloads.py` runs every time on a fixed
subject: Martigny-Verbier on the Swisstopo map, with every map rollout flag
on. It downloads a region from a fresh worker and again after a worker
update, frames a custom area, and runs Sync all.

**Why.** The suite was weekly and blocked nothing, so that someone else's
tile server would not pay for every push. On Monday 9 November 2026 it went
red with "Download failed" on 7 of 11 tests. Nothing was blocked, nobody
looked, and on 12 November Hugo's Martigny-Verbier download failed in
production for two reasons:

- the downloader read its tile sources off the live map, and gave up
  whenever `map.isStyleLoaded()` was false, which MapLibre reports while
  any tile is still loading;
- it listed the `osmclip://` source the `map_beyond_switzerland` flag adds
  (SNOW-1460). The service worker cannot fetch that scheme, so the area
  ended Incomplete.

The fuzzed subject never ran with the owner's flags on, so it could not
have caught the second reason on any day.

Hugo chose this option on 12 November 2026, over gating only releases or
keeping the weekly run and opening an urgent ticket when it goes red.

**Cost.** About ten minutes on a download-path PR, plus a few hundred real
tiles from swisstopo and OpenFreeMap each run. The cancel-in-progress
concurrency group drops a run superseded by a new push.

**Supersedes** the "not a merge gate" rationale that used to head the
workflow file and `docs/offline-assurance.md`.
