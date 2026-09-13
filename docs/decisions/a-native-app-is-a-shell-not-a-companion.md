---
name: a-native-app-is-a-shell-not-a-companion
description: Native companion app rejected — a native build wraps the PWA (WKWebView/Capacitor); background location is its only justification
status: draft
last-reviewed: 2026-09-13
---

# A native app is a shell, not a companion

> **Status: draft.** This records the shape a native app would have to take,
> argued from an idea floated on 2026-09-13 — "a very small local native
> companion app that provides native notifications and background working, to
> track the user and generate a live route". Nobody has accepted building one.
> Flip to `current` if the constraint is adopted; delete it if the idea is
> dropped.

## Decision

Three rules, in the order they have to be answered:

- **Whether to record a track at all is the prior question**, and it reverses
  a written position. [`competitors.md`](../competitors.md) item 13 files
  "GPS tracking, logbook, activity feed" under **Not for us** — Strava's and
  Whympr's ground, "nothing about a bulletin product gets better by adding
  it." Settle that before any of the rest; it is a product decision, not an
  engineering one.
- **Background location is the only capability that justifies going native.**
  Notifications are not a second reason, and a native app built for them
  alone would deliver nothing the site does not already do.
- **If it is built, it is a shell around the existing PWA** — one icon, the
  web app running inside it, a native location plugin bridging into the
  JavaScript that already exists. **A second app alongside the PWA is ruled
  out.**

## Why

**Because the web genuinely cannot do background location, and can do
everything else on the list.** There is no web API for it: `watchPosition`
stops when the page is frozen or the screen locks, and on iOS that is a hard
stop. Continuous tracking from a pocket over a five-hour tour needs Core
Location with background updates and Always authorisation, or an Android
foreground service. *(Platform behaviour as understood at time of writing —
not verifiable from this repo, and worth re-checking against current OS
releases before committing.)*

Nothing in `static/js` calls `watchPosition` today. Geolocation is one-shot
throughout: [`map_geolocate.js`](../../static/js/map_geolocate.js) wraps a
MapLibre `GeolocateControl` with `trackUserLocation: false` and calls
`getCurrentPosition` for a single fix. The app has no continuous tracking of
any kind, so this is a new capability rather than a better version of one.

**Because notifications already ship.** `PushSubscription`
([`apps/accounts/models.py:435`](../../apps/accounts/models.py)),
`push_service.py`, `push_config.py` and the `mint_vapid_keypair` command are
a working Web Push estate ([`push-notifications.md`](../push-notifications.md)),
and iOS has supported Web Push for home-screen PWAs since 16.4. Native would
add a direct APNs token, notification actions, and time-sensitive or critical
alerts. Those are refinements of a solved problem, and none of them is worth
an App Store presence on its own.

**Because a companion app cannot hand the track over.** Two apps on one phone
share no storage on iOS. A separate recorder's only route back to the map is
the server — and the server is precisely what is absent in the terrain where
the track is being recorded, which is the whole premise of the offline estate
([`offline-first.md`](../offline-first.md)). That leaves the companion as
either a recorder that syncs on return — useful, but not *live* — or as the
shell, where the bridge is a function call. The two-icon middle ground buys
two install flows, two permission dialogs, and a track the user cannot see on
the map they are standing on.

**Because "live" has to name an audience.** Live on your own map needs the
bridge, so it needs the shell. Live to other people is the most social
surface the product could have, and SNOW-848 deliberately took the roster,
the count and the going state off trips — see
[`a-trip-is-one-object-with-a-roster`](a-trip-is-one-object-with-a-roster.md),
whose disclosure-rule bullet records it. Broadcasting a companion's position
to other accounts would reverse that within weeks of it landing.

## Consequences

- **The offline estate is the first thing a native spike has to test, before
  a line of Swift.** Service workers in WKWebView are gated behind
  `WKAppBoundDomains`, which constrains an app to declared domains. Snowdesk
  puts more behind the worker than most: [`sw.js`](../../static/js/sw.js),
  per-area pinned Cache Storage buckets
  ([`map_basemap_downloads.js`](../../static/js/map_basemap_downloads.js)),
  `navigator.storage.estimate()` quota reporting in
  [`db.js`](../../static/js/db.js), and `DownloadArea`
  ([`apps/downloads/models.py:103`](../../apps/downloads/models.py)) whose
  tiles live only in each device's own bucket. **Whether that estate survives
  under `WKAppBoundDomains` is unverified** — it is the load-bearing unknown,
  and a spike that defers it is measuring the wrong thing.
- **Release cadence stops being ours.** `main` deploys to staging and
  `release` to production continuously ([`deployment.md`](../deployment.md));
  App Store review does not. A shell that loads the site remotely keeps the
  current cadence and puts only the native layer behind review. A shell that
  bundles the web assets ships every fix through review — which is the
  decision that actually sets the cost, and it should be taken explicitly.
- **A companion app is ruled out.** Do not re-propose the two-app shape
  without first answering how the track reaches the map with no network.
- **Continuous location is a new data class.** Nothing stored today follows a
  person through time; a recorded track does. It needs its own retention,
  sharing and erasure position — written before collection starts, not after.
