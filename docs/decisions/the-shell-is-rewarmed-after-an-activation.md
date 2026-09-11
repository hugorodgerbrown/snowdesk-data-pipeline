---
name: the-shell-is-rewarmed-after-an-activation
description: sw.js activate re-warms the map page and its scripts/styles — _rewarmShell, _shellSubresources, SHELL_PAGE; a deploy broke offline open
status: current
last-reviewed: 2026-09-11
---

# The shell is re-warmed after an activation

**Decision** (SNOW-912). `activate` puts the map page back in the shell
cache it has just emptied. `_rewarmShell()` runs after `clients.claim()`, inside the same
`waitUntil`, and warms `SHELL_PAGE` (`/`) through `_warmCache` — which, for a
same-origin HTML response, now also fetches the page's own same-origin
scripts and stylesheets (`_shellSubresources`, capped at
`SHELL_SUBRESOURCE_LIMIT`, cache-checked before each fetch).

It is bounded by the same offline mode every other network path in this file
consults: a device the user has switched offline, or one that has latched
there, spends nothing. It never throws — an activation that fails leaves the
device with no worker at all, and this is a convenience, not a precondition.

## Why

`activate` deletes every shell cache that is not the version now live, which
is correct and load-bearing: stale HTML pointing at hashed assets that no
longer exist is worse than no HTML at all. What nothing did afterwards was
put the page back. From the moment a deploy activated until the user next
opened `/` **while connected**, the app could not open offline at all.

That gap was silent, it opened on **every** deploy, and no surface reported
it until SNOW-907 shipped the offline-content report — which is how it was
found. A device nine minutes past a deploy, on a train with a working
signal, reported "The app opens: No". The report was right. The user had
been on `/account/settings/` when the new worker activated, so the only page
re-cached was the one they were reading.

The page alone is not enough, and that is the second half of the decision. A
page whose HTML is saved and whose JavaScript is not does not open — it
paints a blank frame, which to the person holding the phone is
indistinguishable from a page that was never saved. Warming the HTML on its
own would have moved the report's "The app opens" row to Yes while leaving
the user with exactly the failure that row exists to catch.

**And the feeds go with it.** `activate` deletes the cached
`/api/` responses along with the rest of the old shell, so a re-warm that
put back only the page left a device that opened to a **grey map** — no
danger ratings, no region outlines. The page itself names the day:
`#season-scrubber`'s `data-today` is server-rendered per request, so a
cached page carries the day it was fetched on for as long as it sits there,
and that is the day its boot puts in the ratings URL. So the invariant is

> a cached page and the feeds its own boot will ask for are cached
> together, or the map opens grey,

and `_warmShellFeeds` holds it by deriving the feed URLs from the page it
has just warmed (`_shellBootFeeds`, `_shellPageDay`). The same hole opens
without a deploy — a flaky connection can land the navigation, which
`_networkFirst` caches, and lose the feed fetches that follow — and this
closes that on the next activation or the next press of the report's Save
control.

Three feeds, not the whole of `COUNTRY_FEED_PATHS`: the dated ratings, the
undated season payload the scrubber and timelapse read, and the region
outlines. The boundary tiers and `/api/resorts.geojson` are loaded by paths
this warm is not standing in for, and widening a repair is how a repair
becomes a second thing to reason about.

Activation was chosen over the two alternatives. Precaching on **install**
runs against the old worker's cache generation, so the page would have to be
re-fetched at activation anyway. Warming **from the page**, after
`controllerchange`, only ever reaches tabs that happen to be open at update
time — and the device that most needs the shell is the one whose owner
updated the app at home and opened it on a lift.

## Consequences

- Every activation costs one page fetch plus whatever that page references
  and the cache does not already hold. On a device that has simply opened
  the app, the subresource pass is one `cache.match` per entry and no
  network at all; the fetches happen on the one occasion they are the point,
  the generation after a deploy.
- The warmed page is stamped with the principal its HTML declares
  (SNOW-624), so it is servable to whoever was signed in when the worker
  activated — and refused, correctly, after a sign-out.
- `_shellSubresources` matches same-origin `.js`/`.css` by URL, not by tag.
  A future shell asset in another form — a module graph reached only through
  `import`, a font the layout blocks on — will not be warmed by this and
  needs adding deliberately.
- The worker now reads `data-today` (`_shellPageDay`) and the report reads
  it too (`pageDay` in `offline_audit_core.js`). A fixture table in
  `tests/js/test_sw.js` holds the two to identical answers: the report
  verifies the feed this warm fetches, so a drift between them puts a green
  row over a day nothing warmed.
- `BOOT_FEED_COUNTRY` (worker) and `BOOT_COUNTRY` (report) both mirror the
  country `map.js` hard-codes in its two boot legs. A grep for either finds
  all three the day that changes.
- The report's "Save the map page" control remains the manual path for a
  device that missed the warm (offline at activation, or a partial run).
- `activate` stops waiting after `SHELL_REWARM_BUDGET_MS`. The bound is on
  this caller rather than on `_warmCache`'s fetches, which must stay
  unbounded for a several-thousand-tile basemap download; a radio that
  hangs rather than rejecting would otherwise hold the activation open.
