---
name: bounded-offline-read-paths
description: sw.js read paths are time-bounded and latch offline — _boundedFetch, OFFLINE_LATCH_THRESHOLD, /livez probe; a dead radio hangs, not rejects
status: current
last-reviewed: 2026-08-28
---

# Offline read paths are bounded, and latch

**Decision.** Every service-worker **read** path (`_networkFirst`,
`_staleWhileRevalidate`, `_basemapStaleWhileRevalidate`) fetches through
`_boundedFetch`, which aborts after a fixed budget. Three consecutive budget
expiries **latch** the worker into an offline mode in which read paths do not
call the network at all. The latch lifts on a bounded probe to `/livez`, on the
`online` event, or on the user's own control — the "Offline mode" row in the
account menu. `_warmCache` is exempt from both — from the **latch**, not from the
user's own offline mode (see below).

**Amended by SNOW-748.** The mode has three values, not two: `auto`, `offline`
(the worker latched itself) and `offline-forced` (the user asked). Everything
below about bounding applies to `offline`. `offline-forced` is bounded by the
user alone — see "The one exception".

## Why

The whole offline story was written as `catch` branches, which assume a failing
network **rejects**. That is true when the radio is off or the request is
refused, and it is what every test and every manual DevTools "offline" check
exercises.

It is not true on the London Underground, in a valley with no coverage, or
behind a captive portal that black-holes traffic. There the radio is *attached*
— `navigator.onLine` stays `true` — but there is no route, so `fetch` neither
resolves nor rejects. It hangs on TCP retries for tens of seconds to minutes.
The catch never runs. The fallback never engages. The app shows a blank page
for minutes while the data it needs sits on disk, and its own downloads panel
(reading IndexedDB, instantly) cheerfully lists 222 MB of downloaded regions
the map cannot show.

That is the bug this decision exists for, reported from an actual Tube journey.

## Why a timeout alone was not enough

The obvious fix is a per-request timeout, and it is necessary but not
sufficient. It converts one multi-minute hang into a 3–5 second hang **per
request, indefinitely**. A day of ski touring out of signal would mean paying
that on every navigation and every uncached tile — better than a blank screen,
and still an app that feels broken for as long as you are out.

So the two mechanisms have distinct jobs, and conflating them is the mistake to
avoid when editing this code:

- **The budgets are the detector.** They exist to convert a hang into the
  rejection the existing fallbacks were already written against, and to feed
  the latch. `NAVIGATION_FETCH_BUDGET_MS` and `SHELL_FETCH_BUDGET_MS` are 5s,
  `BASEMAP_FETCH_BUDGET_MS` is 3s.
- **The latch is the fix.** `OFFLINE_LATCH_THRESHOLD` (3) consecutive timeouts
  and the worker stops asking: a cache hit serves, a miss returns the
  synthesized 504 immediately, indefinitely, at no per-request cost. Any
  successful read resets the counter, so one slow request never latches alone,
  while a map view's burst of tile requests recognises a dead radio in about
  nine seconds.

A **refusal** deliberately does not count toward the latch. A refusal is also
what the browser gives for a blocked request, a CORS failure, or a DNS miss on
an otherwise healthy connection — latching on those would take the app offline
while the network is fine.

## The ordering fix that came with it

Both stale-while-revalidate paths started their `fetch` *before* reading the
cache, so every request — cache hits included — launched a network call. With
no route, a single map view left several hundred hanging requests holding
connection slots, and the requests that genuinely needed the network queued
behind them as sockets timed out one by one. That is what made the failure look
intermittent rather than clean. The cache is now read first; revalidation is
fired unawaited behind a hit, and skipped entirely when there is no route.

## Coming back is the safety-critical half

A latched app is serving **cached avalanche ratings**. Leaving it latched
longer than necessary is the one genuinely dangerous outcome here, and it is
worse than the slowness the latch removes. Three mechanisms bound it, and none
of them should be removed as "noise" without replacing it:

1. A bounded 2s probe to `/livez` on a 30s → 60s → 300s backoff. `/livez`
   (`apps/core/views.py`) does no work, never reads `request.user`, never
   touches the database, and is exempt from `PosthogContextMiddleware`, so it
   measures the route and nothing else. The last backoff step repeats rather
   than growing, so a device left latched overnight still notices signal within
   five minutes.
2. The page's `online` listener, which asks for an immediate unlatch rather
   than waiting out the backoff.
3. The offline banner, which shows a distinct "Offline mode" state whenever the
   worker is latched — including while `navigator.onLine` is `true`, which is
   the case the old banner could not represent — and carries the user's own
   "Try reconnecting" control.

## The one exception: `offline-forced` (SNOW-748)

A user who switches "Offline mode" on gets **none of the three**. No probe is
scheduled, an `online` event does not lift it, and a read-path timeout cannot
convert it into an auto-latch. That is a deliberate hole in the rule above, so
it needs a reason.

**It is not a guess that can be wrong.** The three mechanisms exist because a
latch is the worker *inferring* there is no route, and an inference can outlive
the condition that produced it. `offline-forced` infers nothing. The user has a
route and has decided not to spend it — a metered roam, a battery to nurse, a
tunnel they are about to enter — so there is no discovery for a probe to make.
Probing it is not a safety measure but a countermand: SNOW-742 shipped exactly
that, routing the user's request into `_latchOffline()`, and the probe put them
back in `auto` within thirty seconds.

**The staleness is on screen the whole time.** The reason to bound a latch is
that a user may not know they are reading old ratings. Here they cannot not
know: the struck-through offline symbol is in the header of every page — the
model is a phone's aeroplane mode, where the status bar carries the glyph for
as long as the mode is on — the banner is up throughout with its own
explanation, and its "last synced" phrase counts up live on a 30s ticker. The danger the bounding answers — silent
staleness — is the one state this mode cannot be in.

**Auto-expiry would spend data the user chose not to spend.** Any bound short
enough to matter (an hour, a day) fires precisely when the user is least able
to notice, and the cost lands on the connection they were protecting.

The user's way back is present in both places at all times: the account menu's
"Offline mode" row and the banner's "Use the network again" button.

## Why `_warmCache` is exempt from the latch — and not from a forced mode

A download is a long operation the user explicitly asked for, on a connection
they believe they have, and its failures already reach them (SNOW-568). Cutting
tiles off at 3s would turn a slow download into a failed one. And the latch is
a *guess* drawn from three read timeouts: the guess can be wrong, so a download
the user asked for should still be attempted.

`offline-forced` inverts every clause of that. It is not a guess about the
network, it is an instruction about it; the connection the user "believes they
have" is one they have told the app not to spend. So the forced mode is the one
offline value `_warmCache` honours (SNOW-748): it refuses a new run, and
`_forceOffline()` cancels one already in flight. Both go through the existing
cancellation protocol — a refused or cancelled run reports `cancelled: true`
with `failed: 0`, so no control writes it up as a failed download.

The UI half is the same rule stated once: `snowdesk:connectivity-changed`
carries `navigator.onLine && networkMode === 'auto'` and fires on every mode
change, and `window.pwaConnectivity.isOnline()` answers the same question for
the controls that re-read it when they repaint. Before that the event carried
`navigator.onLine` alone, which is true throughout a forced mode — so the
layers menu kept its green dots and the download controls stayed enabled while
the header symbol said the app was offline.

## Durability

The mode is mirrored to `meta:app` under `network.mode`, and it is restored by
**two** paths — which one applies depends on which mode it is.

**The page re-asserts on boot.** `pwa_offline.js` reads the row and posts it to
the worker, carrying *which* offline mode it was: an `offline-forced` row
re-asserted as `offline` would hand the worker a latch, and the probe that
comes with it would end the user's choice on the next page load. This is the
fast path, and the only one that restores an auto-latch.

**The worker re-reads the row itself** (`_hydrateNetworkMode`, SNOW-748),
started at **script evaluation** and memoised in a single promise that every
consumer of the mode awaits: `_shouldUseNetwork()`, `_warmCache`'s refusal, and
the `network-mode` message handler's reply. The earlier
version of this document said the worker never read the row on its own, and
that was the bug: `_networkMode` is module scope, Chrome terminates an idle
worker after about thirty seconds, and a **user-forced** mode was therefore
silently lost. The restarted worker came back in `auto`, resumed using the
network, and fired no `online`/`offline` event at all — so nothing corrected
the header symbol, which went on showing the app offline over one that was back
on the wire. Reproduced twice in a browser and confirmed with an
instrumented run: `workerMode: "auto"`, `persistedUserChoice: "offline-forced"`,
`pageIsOnline: true`, no interface events. It survived review because polling
the worker to check on it is exactly what keeps it from being recycled.

Recovery cannot depend on a page being there to push the mode, so the worker
recovers it — and it must start that read at evaluation rather than on the
first read path. A worker wakes for four reasons (`fetch`, `message`, `push`,
`sync`) and only the first consults a read path, so a lazily-hydrating worker
asked for its mode by a bare `postMessage({type: 'network-mode'})` still
answered `'auto'` — and that answer is acted on, not merely observed:
`pwa_offline.js` treats any `network-mode` announcement as authoritative and
repainted the user's mode off. Hence also the handler awaiting hydration
before it replies. `activate` is not the hook for any of this: it fires on
install and update, not on the idle restart that loses the mode. This is the same worker-side IndexedDB read `_currentPrincipal()`
has always done on the navigation path, with the same shape: `try`/`finally`
`db.close()`, and fail-safe to `auto` on any error, so a DB the worker cannot
read never wedges it offline. Once hydration resolves, the recovered mode is
published to every client, so a page that was open across the restart resyncs
its symbol and its menu row rather than continuing to show a stale mode.

**Only `offline-forced` is recovered this way.** A persisted `offline` is the
worker's own inference from three read-path timeouts, and by the time a
recycled worker reads it back, the radio it was inferred from may have been
alive for hours. Restoring it would strand the user offline on expired
evidence, with nothing to clear it but a probe on a backoff that reaches five
minutes; declining to restore it costs a re-latch within about nine seconds if
the radio really is still dead. Detection is cheap and self-correcting here,
and restoration is not. A forced mode has no evidence to expire — it is the
user's standing instruction, and only the user ends it. Durability is also the
whole difference between a setting and a gesture: unlike a latch, nothing
re-establishes a forced mode if the row is lost.

A live page's `network-mode` message outranks the row, exactly as an explicit
`register-basemap-origins` message outranks `_hydrateBasemapOrigins`. The row
is only as fresh as its last write, so a user pressing "use the network again"
while the read is in flight must not be forced offline again by it.

## Consequences

- The 3s tile budget will abort some slow-but-alive fetches on a genuinely poor
  connection. MapLibre retries and overzooms on a failed tile, so the cost is a
  retry rather than a hole — but this and `OFFLINE_LATCH_THRESHOLD` are the two
  numbers most likely to need tuning against real-world use.
- `_boundedFetch` uses an explicit `AbortController` + `setTimeout` rather than
  `AbortSignal.timeout`. It is driveable by a test's fake clock (the latter's
  internal timer is not), and it works on every browser that has a service
  worker at all.
- DevTools' "Offline" checkbox **does not reproduce** the bug this fixes — it
  makes fetches reject, which is the case that always worked. Reproducing it
  needs a hang: request blocking plus a high-latency throttle, or a real tunnel.
