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
`online` event, or on the user's own control — the network toggle in the nav
header. `_warmCache` is exempt from both.

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

A user who presses the header toggle gets **none of the three**. No probe is
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
know: the toggle is pressed and struck through in the header of every page, the
banner is up throughout with its own explanation, and its "last synced" phrase
counts up live on a 30s ticker. The danger the bounding answers — silent
staleness — is the one state this mode cannot be in.

**Auto-expiry would spend data the user chose not to spend.** Any bound short
enough to matter (an hour, a day) fires precisely when the user is least able
to notice, and the cost lands on the connection they were protecting.

The user's way back is present in both places at all times: the header toggle
and the banner's "Use the network again" button.

## Why `_warmCache` is exempt

A download is a long operation the user explicitly asked for, on a connection
they believe they have, and its failures already reach them (SNOW-568). Cutting
tiles off at 3s would turn a slow download into a failed one.

## Durability

The mode is mirrored to `meta:app` under `network.mode` and **re-asserted by
the page on boot**, rather than read by the worker on its own fetch path. A
worker terminated while idle comes back in `auto` having forgotten the latch;
re-asserting restores it, at the cost of one message instead of an IndexedDB
read per navigation. If the re-assertion is lost, the latch simply re-trips
within about nine seconds.

SNOW-748: the re-assertion carries **which** offline mode, not just that there
was one. A `offline-forced` row re-asserted as `offline` would hand the worker
a latch, and the probe that comes with it would end the user's choice on the
next page load. Durability is also the whole difference between a setting and a
gesture here — unlike a latch, nothing re-establishes a forced mode if the row
is lost.

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
