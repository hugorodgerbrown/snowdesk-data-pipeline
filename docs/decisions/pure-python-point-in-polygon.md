---
name: pure-python-point-in-polygon
description: region_for_point and point_in_polygon are pure-Python ray-casting on the request path, not dev-only Shapely; classify_match is gone
status: current
last-reviewed: 2026-09-08
---

# Pure-Python point-in-polygon on the request path

> **Retired half (SNOW-802/805).** The subscribe and add-region handlers
> this record describes are gone: a `Subscription` was a bookmark on a
> region and is a region pin now, so nothing classifies a subscriber's
> geolocation. SNOW-805 dropped the table, and `classify_match`, the four
> match-kind constants and the drift guard that pinned them to
> `Subscription.GeoMatchKind` went with it. What survives — and why the
> record stays `current` — is the first decision: `region_for_point` in
> `apps/regions/services/point_match.py` is the request-path
> point-in-polygon for a dropped favourite pin (`apps/favourites/services
> .create_favourite`) and for a GPS-gated field report
> (`apps/observations/views.py`), and it stays pure Python for the reasons
> below.

**Decision.** The subscribe and add-region request handlers classified a
subscriber's geolocation relative to the target MicroRegion using a
pure-Python ray-casting implementation in
`apps/regions/services/point_match.py`, not the Shapely-based helper used by
`audit_resort_regions`. Raw geo and language fields (country, city, lat/lon,
`accept_language`, primary language) were read from
`Subscription.subscribed_via` (a FK to `core.RequestLog`) rather than being
duplicated onto `Subscription`.

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
(sign-up, sign-in, subscribe, add-region, share-click). `Subscription` reached
them through the existing `subscribed_via` FK, frozen at the moment the row was
created. Duplicating the fields onto `Subscription` would have required a
ten-column migration and would have diverged from the single source of truth
without adding information. `Account.acquisition_request` is the one such FK
left, and it follows the same rule.

**Consequences.**

- `regions` must not import `accounts`. The constraint held by keeping the kind
  constants in `point_match.py` as plain strings that
  `Subscription.GeoMatchKind` mirrored, with a unit test guarding the drift;
  both sides went with SNOW-805, and the rule they served still stands for
  anything `regions` grows next.
- On-boundary behaviour of the ray-casting algorithm is implementation-defined.
  This is documented and tested but not over-indexed — boundary ambiguity at
  kilometre-scale geo accuracy is a non-issue.
- Any future caller that needs precise topological operations (intersection,
  union) should continue to use Shapely via `audit_resort_regions`'s pattern
  of a lazy optional import.
