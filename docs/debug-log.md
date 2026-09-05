---
name: debug-log
description: On-device debug trace — window.pwaDebugLog, log:debug store, debug_log flag (GRP_DEBUG), sw classify/pinned.search, req serve request ledger
status: current
last-reviewed: 2026-09-03
---

# Debug log (SNOW-812)

A client-side diagnostic trace you can read **on the device that produced
it**, with no devtools attached.

## Why it exists

The map page is the hardest surface in the project to debug, for two
reasons that compound:

1. **Half its decisions happen in the service worker.** Which origins are
   allowlisted for caching, which pinned buckets exist, whether a tile hit
   any of them — none of that is reachable from a phone.
2. **Every one of those decisions is a deliberately silent fallback.**
   `static/js/map.js` has 39 `catch (_e) {}`-shaped sites and
   `static/js/sw.js` has 27. That silence is correct — a failing cache
   probe must never break the page — and it is exactly why a broken
   offline download looks identical to a working one until the map is
   blank.

The motivating symptom: *a region reports as downloaded but does not
appear on the map*. Six things must all hold, and none of them was
observable.

| # | Link in the chain | Trace line |
|---|-------------------|-----------|
| 1 | `basemap.regions` row in `meta:app` holds this `region_id` | `idb region.record` |
| 2 | Records and pinned buckets on disk agree | `cache areas.reconcile` |
| 3 | The region is in the loaded GeoJSON | `map hash.resolve` |
| 4 | Its country's feeds actually arrived | `net country.load` |
| 5 | The SW still has its basemap-origin allowlist | `sw origins.hydrate`, `sw classify` |
| 6 | A tile lookup hits one of the pinned buckets | `sw pinned.buckets`, `sw pinned.search` |
| 7 | Every request that was served, and by which partition | `req serve` (SNOW-846) |

Read top to bottom, those answer the question. The most common real
answer is #5: the browser terminates an idle worker, `_basemapOrigins`
goes with it, `_hydrateBasemapOrigins()` fails to get it back, and every
tile request is classified `unclassified` over a cache that is full —
the SNOW-722 failure mode.

## Two channels: decisions (`sw`) and the request ledger (`req`)

SNOW-846 splits the worker's output in two, because they answer different
questions at different volumes.

**`sw` — decisions.** The original trace: which origins are allowlisted,
which pinned buckets exist, whether a tile hit one, what a download run
did. A handful of lines per interesting moment.

**`req` — one line per request the worker serves**, answering *content
did not appear; where was it trying to get it from?* Each line carries the
URL, the strategy that ran, **the partition that answered**, the HTTP
status and elapsed ms:

```
req  serve  url=…/14/8522/5829.pbf  strategy=basemap  source=pinned   status=200  ms=3
req  serve  url=…/14/8520/5827.pbf  strategy=basemap  source=network  status=200  ms=412
req  serve  url=/map/               strategy=navigate source=offline-html status=200 ms=8
req  serve  url=/api/ratings.json   strategy=network  source=passthrough status=null ms=null
```

| `source` | Means |
|---|---|
| `shell` | the versioned shell cache (`CACHE_VERSION`) |
| `passive` | `BASEMAP_CACHE`, filled by ordinary browsing |
| `pinned` | a deliberate download's bucket — the adjacent `sw pinned.search` line names **which** |
| `network` | a real round trip; `status` is its own |
| `offline-html` | the pre-cached fallback page stood in for the page you asked for |
| `timeout-504` | synthesized: every partition missed and there was no route, or a budget expired |
| `network-error` | the fetch rejected with nothing cached to fall back to |
| `passthrough` | the worker issued no `respondWith` — the browser fetched it natively |
| `cache` | the SNOW-722 read-only probe answered; the adjacent line says from where |
| `dev-bypass` | `DEV_SHELL_BYPASS` with the opt-in off |

Two properties worth knowing before you read one:

- **`passthrough` has no outcome.** Same-origin requests that are neither
  a shell asset nor a navigation (`/api/*`, chiefly) are deliberately not
  intercepted — the worker issues no `respondWith`, and it must stay that
  way, because putting it on the path of every API call is a real cost to
  pay for a diagnostic. The line records the decision, and "the worker
  never saw this; it went straight to the network" is itself the answer
  when a feed comes up empty offline.
- **Volume.** A map pan is hundreds of lines and the ring holds 500, so
  `req` will evict `sw` lines if you leave both on. That is why it is its
  own source: use the panel's filter to read one channel at a time.

## Using it

You need the `debug_log` waffle flag, which is scoped to the **GRP_DEBUG**
group. Then, on any page:

1. Tap the small `▚` pill, bottom-left.
2. Tick **Record**. Nothing is recorded until you do — see "Cost" below.
3. Reproduce the problem.
4. **Copy** puts the whole trace on the clipboard as fixed-width text,
   ready to paste into a Linear comment from the phone itself.

The panel survives a reload: lines are persisted, and reopening the panel
after a reload shows the trace from before it. Rows are coloured by
outcome, not by source — a miss, an error or a `found=false` reads red
whichever producer emitted it, because reading a trace is scanning for
the line that went wrong.

**Clear** drops the trace and keeps recording. Un-ticking **Record** drops
it too: a trace is a point-in-time diagnostic, and one left on the device
after the session that wanted it just means the next reader is looking at
somebody else's afternoon.

## Cost, stated exactly

- **Without the flag**: `base.html` renders neither the panel nor the two
  scripts, so `window.pwaDebugLog` is undefined and every instrumented
  call site is an optional-chain no-op.
- **The gate itself**: two guards in
  `apps/public/context_processors.py`, both needed. It is a
  `SimpleLazyObject`, so a response that never renders `base.html` (a JSON
  API endpoint, an HTMX fragment) pays no query; and it checks
  `request.user.is_authenticated` before consulting waffle, so an
  anonymous visitor never reads the flag row. Without that second guard
  `monitor_query_counts` reads `home` at 5 → 8 — the same cost that got a
  flag deleted before merge in SNOW-749. See
  [`feature-flags.md`](feature-flags.md) for the full measurement and for
  the one configuration the short-circuit does not support
  (`everyone = Yes`).
- **With the flag, not recording**: `record()` returns on its first
  statement. Callers whose detail costs something to build check
  `window.pwaDebugLog.on` first.
- **Recording**: lines land in an in-memory ring and flush to IndexedDB in
  batches (1s on the page, 250ms for the worker's postMessage). One
  transaction per burst, never one per line.

## Shape

```
window.pwaDebugLog.record(src, evt, detail)
```

`src` is the producer — `map`, `sw`, `idb`, `cache`, `net`, `app` — and
drives the panel's filter. `evt` is a dotted name. `detail` is a small
JSON-serialisable object of identifiers and outcomes: keys looked up,
cache names searched, hit/miss. **Never a response body** — a trace has to
be readable on a phone screen.

## Where the pieces live

| Piece | File |
|-------|------|
| Recorder (`window.pwaDebugLog`) | `static/js/debug_log.js` |
| Panel | `static/js/debug_log_panel.js`, `templates/includes/_debug_log_panel.html` |
| Store (`log:debug`, schema v5, newest 500) | `static/js/db.js` |
| Worker emitters + `_debugLog()` | `static/js/sw.js` |
| Flag gate | `apps/public/context_processors.py`, `apps/core/fixtures/waffle_flags.json` |

## The three-way enabled mirror

The recording flag is persisted the same way `pwa_dev_shell_toggle.js`
persists `sw.devShellCache`, and for the same reason:

- **localStorage** — the page's own synchronous read at parse time, so
  recording is live before the boot sequence worth tracing goes past.
- **A `debug.enabled` row in `meta:app`** — so a service worker the
  browser terminated and later restarted for a bare `fetch` event can
  rehydrate the flag itself (`sw.js`'s `_hydrateDebugLogEnabled()`).
- **A `debug-log-enabled` postMessage** — so the live worker reacts now
  rather than after a restart.

`debug_log.js` re-asserts the flag on every page load, which is what
covers a worker restarted since the toggle was last flipped.

## Why the worker relays rather than writing

`sw.js` broadcasts `{type: 'debug-log', entries: [...]}` to its clients and
`debug_log.js` folds those into the same ring the page writes to. One
writer on the store, one ordering of the trace, and the worker keeps no
IndexedDB handle it would otherwise need. A worker with no live client
discards its buffer rather than holding it — an uncapped buffer in a
worker woken for a background sync is a leak, and a trace nobody was
watching for is not worth one.
