---
name: an-update-carries-your-downloaded-content-across
description: sw.js activate carries downloads into the new shell cache — _carryShellContent, SHELL_CARRY_BUDGET_MS, pwa.sw.content_carried
status: current
last-reviewed: 2026-10-30
---

# An update carries your downloaded content across

**Decision** (SNOW-1311). Before `activate` reaps anything, and before
`clients.claim()`, `_carryShellContent()` in `static/js/sw.js` copies the
user's content from every old `snowdesk-shell-*` cache into the
`CACHE_VERSION` cache:

* **What.** Every same-origin entry — bulletin pages, weather sheets
  (`public-json`), trip routes (`private-json`), `STATIC_PATHS` feeds such
  as `/api/ratings/`. The one exception is an old static asset (`'static'`
  by an extension in `STATIC_SHELL_EXTENSIONS`, not a `STATIC_PATHS` feed)
  that no carried HTML page references, found with `_shellSubresources`
  over each carried page.
* **As stored.** `cache.put(request, oldResponse)`, so an `X-SW-Principal`
  stamp survives and no read path changes. Another principal is refused a
  carried private entry exactly as before.
* **Never over the new install.** An entry the new cache already holds is
  skipped.
* **Date precedence.** For a url held by several old caches, the copy
  with the newest `Date` header wins; a missing or unparseable `Date`
  loses to a present one; a tie keeps the first seen. Never enumeration
  order.
* **Complete or keep.** The carry resolves to `{result, copied, skipped,
  failed, bytes}`. Old shell caches are reaped only on `complete`. A
  failed put, a `QuotaExceededError` or `SHELL_CARRY_BUDGET_MS` (30 s)
  expiring is `incomplete`, and **every** old shell cache is kept for the
  next activation to finish. `map-shell-*`, old basemap versions and the
  legacy pinned cache are reaped as before.
* **Reported.** `_debugLog('shell.carry', …)` and
  `pwa.sw.content_carried` (`cache_version`, `result`, `copied`, `failed`),
  only when there was an old shell cache to carry from.

## Why

Every deploy changes `CACHE_VERSION`, and `activate` deleted every older
shell cache. That cache is where `_warmCache` writes everything an area
download saved, and every read path opens `CACHE_VERSION` only. Since
SNOW-1025 a routine update applies silently the next time the tab is
hidden — typically the evening before a tour, when the user has just
downloaded their area. They lost signal next morning with a map and no
conditions, and no surface said so.

A second, version-independent content cache would also have fixed it, but
it would change every read path and every principal check. Carrying the
entries as stored changes `activate` only.

**Hashed assets.** A carried page names its own `map.<hash>.js`; the new
install's assets have different names, so the page needs its old ones to
draw offline. Assets no carried page names are dead weight, and are left.

**Complete or keep.** An old shell cache is the only copy of whatever the
carry did not reach. Reaping it after a partial carry would be the bug this
fixes, made intermittent.

**Quota abort** (Hugo's decision). Once the origin is out of quota every
later put fails the same way, and the old caches are kept regardless. Stop
at the first one rather than spending the budget on certain failures.

**No expiry** (Hugo's decision). Content is carried whatever its age.
Downloads hold only today onward since SNOW-1312, so what a carry moves is
bounded by what the user downloaded, and a dated url is never served for
another day.

## Consequences

* An activation can take up to `SHELL_CARRY_BUDGET_MS` longer before it
  claims. A silent update applies while the page is hidden or as a fresh
  tab loads, so in practice nobody watches it.
* Disk briefly holds two copies of the carried content, which is also why
  quota can bite here and nowhere else.
* An old `/map/` page is carried with its old assets. Online, `_rewarmShell`
  replaces it straight away; offline it is the last page that worked, as it
  was before the update.
* A device that never completes a carry (permanently short of quota) keeps
  accumulating old shell caches. `pwa.sw.content_carried` with
  `result: incomplete` is the figure that would show it.
* Supplements
  [`the-shell-is-rewarmed-after-an-activation.md`](the-shell-is-rewarmed-after-an-activation.md):
  the carry runs first, so the re-warm's cache checks find the carried
  assets and skip them.
