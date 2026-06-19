---
name: pure-python-point-in-polygon
description: Subscribe request path uses pure-Python ray-casting instead of dev-only Shapely; raw geo/language fields live on RequestLog not Subscription
status: current
last-reviewed: 2026-06-14
---

# Pure-Python point-in-polygon on the request path

**Decision.** The subscribe and add-region request handlers classify a
subscriber's geolocation relative to the target MicroRegion using a
pure-Python ray-casting implementation in
`regions/services/point_match.py`, not the Shapely-based helper used by
`audit_resort_regions`. Raw geo and language fields (country, city, lat/lon,
`accept_language`, primary language) are read from `Subscription.subscribed_via`
(a FK to `core.RequestLog`) rather than being duplicated onto `Subscription`.

**Why — no Shapely on the request path.** Shapely is declared as a dev-only
dependency in `[dependency-groups]` `dev`. It is used lazily by
`audit_resort_regions` (an offline management command) via `importlib`, so it
never needs to be present at runtime. Promoting it to a production runtime
dependency would add a compiled C extension to the Render deployment image for
a task that a 20-line algorithm handles adequately. GeoLite2 coordinates carry
kilometre-scale accuracy anyway, so the sub-metre precision difference between
ray-casting and Shapely's GEOS bindings is irrelevant in practice.

**Why — geo fields on RequestLog, not Subscription.** `core.RequestLog` already
captures all ten geo and language fields at every request inflection point
(sign-up, sign-in, subscribe, add-region, share-click). `Subscription` gains
access to them via the existing `subscribed_via` FK, which is frozen at the
moment the row is created. Duplicating the fields onto `Subscription` would
require a ten-column migration and would diverge from the single source of truth
without adding information.

**Consequences.**

- `regions` must not import `subscriptions`. The kind constants in
  `point_match.py` are plain strings (`in_region`, `in_neighbour`, `elsewhere`,
  `unknown`); `Subscription.GeoMatchKind` mirrors them with identical literal
  values. A unit test guards against drift.
- On-boundary behaviour of the ray-casting algorithm is implementation-defined.
  This is documented and tested but not over-indexed — boundary ambiguity at
  kilometre-scale geo accuracy is a non-issue.
- The `neighbours` queryset is evaluated lazily in `classify_match`; the first
  match short-circuits the loop. The query overhead is bounded by the neighbour
  count, which is small (median ~5 per Swiss micro-region).
- Any future caller that needs precise topological operations (intersection,
  union) should continue to use Shapely via `audit_resort_regions`'s pattern
  of a lazy optional import.
