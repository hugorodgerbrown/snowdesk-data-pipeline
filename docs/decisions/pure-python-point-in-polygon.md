---
name: pure-python-point-in-polygon
description: region_for_point and point_in_polygon are pure-Python ray-casting because km-scale accuracy suffices; Shapely IS a runtime dep, not dev-only
status: current
last-reviewed: 2026-09-11
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

**Why — ray-casting is enough, not because Shapely is unavailable.** GeoLite2
coordinates carry kilometre-scale accuracy, so the sub-metre precision
difference between ray-casting and Shapely's GEOS bindings is irrelevant in
practice, and a 20-line algorithm over the raw GeoJSON beats constructing a
geometry object per request for a containment test this coarse.

> **Corrected 2026-09-11.** This paragraph previously argued that Shapely was
> "a dev-only dependency in `[dependency-groups]` `dev`" which "never needs to
> be present at runtime", and that promoting it "would add a compiled C
> extension to the Render deployment image". Both halves are now false and the
> second was overtaken first: **SNOW-323 promoted Shapely to an ordinary
> `[project]` runtime dependency** when grouping dissolves started running at
> ingest time (see `apps/regions/fixture_utils.py`). GEOS is in the deployment
> image regardless. Shapely is on a request path today — the Douglas-Peucker
> simplify in `apps/routes/services/gpx.py` runs on GPX upload — so the
> paragraph's original heading was untrue as written. The decision to keep
> `region_for_point` pure Python survives on the accuracy argument alone,
> which is why this record stays `current`.
>
> Function-local Shapely imports remain the project convention, but for a
> different reason than availability: Shapely pulls in GEOS, and a lazy import
> keeps that off the module import path. That convention is documented in
> `apps/regions/services/basemap_tiles.py`.

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
  of a lazy, function-local import. Lazy for GEOS import cost, not because
  the package might be absent — it is a declared runtime dependency.
