---
name: pure-python-point-in-polygon
description: region_for_point is pure-Python ray-casting on the request path, not dev-only Shapely; the Subscription geo fields it fed are retired
status: current
last-reviewed: 2026-09-03
---

# Pure-Python point-in-polygon on the request path

> **Retired half (SNOW-802/805).** The subscribe and add-region handlers
> this record describes are gone: a `Subscription` was a bookmark on a
> region and is a region pin now, so nothing classifies a subscriber's
> geolocation and `Subscription.subscribed_via` / `GeoMatchKind` go with
> the table. What survives — and why the record stays `current` — is the
> first decision: `region_for_point` in
> `apps/regions/services/point_match.py` is the request-path
> point-in-polygon for a dropped favourite pin (`apps/favourites/services
> .create_favourite`), and it stays pure Python for the reasons below.

**Decision.** The subscribe and add-region request handlers classified a
subscriber's geolocation relative to the target MicroRegion using a
pure-Python ray-casting implementation in
`apps/regions/services/point_match.py`, not the Shapely-based helper used by
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
