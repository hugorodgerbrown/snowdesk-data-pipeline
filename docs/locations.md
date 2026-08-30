---
name: locations
description: Coordinate reference — Location, Favourite, Resort, FieldObservation, MicroRegion.centre, Route.points, GeoIP; apps/core/geo haversine
status: current
last-reviewed: 2026-08-23
---

# Locations — what every coordinate in this codebase means

Snowdesk stores coordinates on several models in different shapes. Each
shape is precise, derived or estimated for a reason, and until this
document those reasons lived in scattered docstrings and in the heads of
whoever wrote them.

This is a reference, not a decision. The decisions it points at are
[`location-is-the-primitive`](decisions/location-is-the-primitive.md) and
[`pure-python-point-in-polygon`](decisions/pure-python-point-in-polygon.md);
this file says what each field *is*, and links rather than restates.

Read it before adding a coordinate-bearing field, and before concluding
anything from one you did not write.

## The question this answers

For any coordinate on any model: **is it exact or approximate, who derived
it, and what may I safely conclude from it?**

| Model · field | Shape | Exact? | Derived by |
|---|---|---|---|
| `Location.latitude/longitude` | **The primitive** | Exact; immovable but correctable | Whoever curated or minted the row |
| `Location.elevation_m` | Derived | Approximate | `fetch_elevation` (Open-Meteo) |
| `Favourite.latitude/longitude` | Precise, user-supplied | Exact | The user, dropping a pin |
| `Favourite.elevation` | Derived | Approximate | `fetch_elevation` (Open-Meteo) |
| `Resort.latitude/longitude` | Precise, curated | Exact as a *village* | Geocoder, on the resort's **name** |
| `FieldObservation.latitude/longitude` | Report location | Exact as *reported* | The user, possibly by dragging |
| `FieldObservation.gps_latitude/gps_longitude` | Raw device fix | Exact as *measured* | The device |
| `MicroRegion.centre` (and `SubRegion` / `MajorRegion`) | Derived centroid | Approximate | Polygon centroid, `refresh_eaws_fixtures` |
| `Route.points` / `Route.bounds` | Polyline | Exact, simplified | Uploaded GPX, Douglas-Peucker thinned |
| `RequestLog.latitude/longitude` | IP-estimated | Approximate | MaxMind GeoLite2-City |

## The shapes

### The primitive — `Location`

Since SNOW-700, everything below that is a **place** feeds a `Location`
(`apps/locations/`). Its `latitude`/`longitude` are exact and immovable in
normal operation: a place at a different coordinate is a different location,
and nothing in the request path moves one.

Two paths do write them, and both are corrections rather than movement —
the admin, and the in-map curation editor (`?edit=locations`, SNOW-755).
Correcting a mis-placed pin re-places the same row: the resorts linked to it
were never wrong, only the coordinate was, so the links stay and the derived
`elevation_m` is cleared for re-resolution. **The editor
rounds what it writes to five decimals** (~1 m), because that is what
`apps/locations/data/locations.tsv` carries and a database value finer than
the sheet would make every `import_locations` run report a phantom update.
The admin does not round, so a coordinate set there keeps its full
precision — and a rename in the editor leaves it alone rather than
truncating it.

A **curated place is a `Location` that has a `name`**. There is no separate
model for one, which is the whole point — Mont Fort is a single row that
Verbier, Nendaz, Veysonnaz and Thyon all reference through `ResortLocation`,
so the sharing falls out of the model instead of being four copies of the
scalar `3330`. A location minted from a favourite or an observation carries
no `name` and no `kind`, and is an anonymous point like any other.

`elevation_m` is nullable and resolved out-of-band, because resolving it
needs an Open-Meteo call that cannot ride on a model save.

**A row exists for a place we keep.** A live GPS fix and a GPX trackpoint
resolve *against* locations without minting one. "Everything is a location"
means every *place*, not every *coordinate* — `Route.points` below is the
standing example.

`Location.kind` (`VILLAGE`/`MID`/`PEAK`) describes the place;
`ResortLocation.role` (`BASE`/`MID`/`TOP`) describes what it is to one
resort. They are not duplicates: one point can be a small area's top and
Verbier's mid-station.

The sections below describe what *feeds* a location, and what the migration
tickets (SNOW-696, SNOW-704, SNOW-709) are moving onto it.

### Precise, user-supplied — `Favourite`

`latitude`/`longitude` are exactly where the user dropped the pin. Nothing
rounds them and nothing should.

The pin's **name** is the user's own text, which is why `favourite_detail`
is `sharing=False` with `Cache-Control: private, no-store` — a saved place
must not be indexed. Note what this does *not* say: the coordinate is not
anonymised anywhere. See
[`location-is-the-primitive`](decisions/location-is-the-primitive.md),
which corrected exactly that misreading.

`elevation` is not user-supplied. It is resolved once from the pin's own
coordinates via `fetch_elevation`, so it is the ground height Open-Meteo
reports there — approximate, not surveyed.

### Precise, curated — `Resort`

`latitude`/`longitude` carry `geocode_source`, `geocode_confidence`,
`geocoded_at` and `needs_review`, so provenance is on the row.

**Say plainly what it denotes: the geocoder's answer for the resort's
*name*, which in practice is the village.** Live lookups put Verbier at
1436 m, Nendaz at 1249 m and Veysonnaz at 1231 m, against terrain running
to 3330 m. That is the right figure to lead with — it is where someone
arrives — but it is one point standing for an area that spans two kilometres
of vertical, and reading it as "the resort's elevation" is wrong.

This is the fact `Location` exists to fix: a resort is an area, and an area
needs several locations. See
[`location-is-the-primitive`](decisions/location-is-the-primitive.md).

### Dual, with provenance — `FieldObservation`

The only model carrying two coordinate pairs, and the distinction is
load-bearing for a safety report:

- `latitude`/`longitude` — where the report says it happened, possibly after
  the user dragged the pin.
- `gps_latitude`/`gps_longitude` — the raw device fix.
- `location_source` — `GPS` / `GPS_REFINED` / `MANUAL`.
- `accuracy_radius_km` — how far to trust it.

Since SNOW-709 the report coordinate also reaches a `Location`, and several
observations sharing one is correct — that is what makes "three reports near
Mont Fort this week" a join rather than a distance scan over every row. The
provenance fields above stay on the report: they are data about the
*report*, not about the place.

The gap between the two pairs is the difference between "I was standing
here" and "I tapped roughly here", and it is recoverable by subtraction.
**This is a precision model, never an anonymisation one** — nothing here
obscures a position, and no change should be justified on the grounds that
it does.

### Derived centroid — `MicroRegion.centre`, and its `SubRegion` / `MajorRegion` siblings

Stored as JSON `{"lon": float, "lat": float}`, computed from the region
polygon by `refresh_eaws_fixtures`. It represents the region, not a place
anyone goes, and it sits at whatever elevation the centroid happens to fall
at rather than at a meaningful one.

What follows: anything derived from a centroid means "somewhere in this
region", and any surface showing it must say so. A centroid is the right
anchor for a region — the multi-point model does not extend upward from
resorts to regions — but only if the surface names the elevation it
represents.

Since SNOW-696 the centroid also reaches a `Location`
(`MicroRegion.centroid_location`), minted by
`link_region_centroid_locations`. That location is **anonymous**: naming it
would put it in the curated estate `import_locations` owns, and ask a
curator to maintain a point nobody goes to.

That command derives the point from `boundary` rather than reading the
`centre` column (SNOW-765) — same polygon, same `centre_from_bbox`, so the
same value, but it leaves `centre` with no readers that the estate depends
on. This is what lets SNOW-766 drop the column.

**`centroid_location` is derived, not durable.** `bin/build.sh` reloads the
EAWS fixtures on every deploy, and `loaddata` resets every column those
fixtures do not carry — so this FK is NULL on all 461 regions at the start
of every deploy, and the orphaned `Location` rows behind it accumulate.
Both build scripts re-link immediately afterwards, which is affordable
because the whole derivation is offline: coordinate from `boundary`,
elevation from `MicroRegion.centroid_elevation_m`, both in the fixture
(SNOW-771). Never write code that assumes a value here survives a deploy —
read [`runbooks/region-centroid-backfill.md`](runbooks/region-centroid-backfill.md)
before changing anything in that path.

Region *membership* is a separate question, answered by ray-casting in
`apps/regions/services/point_match.py::region_for_point`. See below.

### Polyline — `Route.points`

A JSON array of `[lon, lat, ele]` triples in **GeoJSON axis order — longitude
first** — with `bounds`, `distance_m`, `ascent_m` and `descent_m` derived
from the full-resolution track before thinning. Capped at `MAX_POINTS`
(2,000) by Douglas-Peucker.

These are **geometry, not places**: a trackpoint never becomes a row.
`Route.points` is the standing example of the rule that "everything is a
location" means every *place*, not every *coordinate*. The uploaded file
itself is parsed and dropped
([`gpx-uploads-are-parsed-not-stored`](decisions/gpx-uploads-are-parsed-not-stored.md)).

### IP-estimated — `RequestLog`

`country_code`, `subdivision_code`, `city`, `latitude`, `longitude` and
`accuracy_radius_km` from MaxMind GeoLite2-City. Approximate by
construction, frequently a city or ISP centroid, and **never a user
position**. Fine for "which country is this request from"; not evidence of
where anybody is.

## Three rules the code follows and never stated

**1. Elevation is always derived, never supplied.**
`apps/locations/services/elevation.py::fetch_elevation` resolves it from a
lat/lon, and it is stored on `Location.elevation_m` so the lookup happens
once. No surface asks a user for an elevation, and no import should accept
one.

**2. Region membership is best-effort.** `region_for_point` may return
`None` — outside coverage, inside a polygon gap, or on a boundary. Both
`Favourite.region` and `FieldObservation.region` document themselves as
best-effort. A null region is a normal outcome, not an error to raise on.

**3. A bulletin's elevation bands are ranges, not points.** "Above 2200 m"
is a band over a whole region. It is not a location, has no coordinate, and
must not be modelled as one.

## Distance

One implementation, `apps/core/geo.py`:

```python
from apps.core.geo import haversine_km, haversine_m
```

Pure Python — no Shapely, no PostGIS — because the observations and MCP
callers run on the request path, the same constraint that produced
[`pure-python-point-in-polygon`](decisions/pure-python-point-in-polygon.md).
Spherical, so ~0.5% at continental distances, which is far below the
precision of anything it compares here.

Arguments are `(latitude, longitude)` — **latitude first**, per SNOW-426 —
which is the opposite of `Route.points`. GPX code swaps at the call site
rather than this module offering a second argument order.

Before SNOW-708 this formula existed four times, in weather, observations,
the MCP resolvers and the GPX parser, each with its own earth-radius
constant and a comment acknowledging the others. If you find yourself
writing a fifth, import this one.

## Choosing a shape for new work

- A user placed it → **precise**, stored exactly, no rounding.
- It fans out to an upstream API you pay per call → **quantised and
  shared**, and register the referent in `active()`.
- You inferred it → **derived with provenance**: store how, and store how
  far to trust it.
- It is a path rather than a place → **polyline**, in a JSON column, and no
  rows.

And the prior question, since
[`location-is-the-primitive`](decisions/location-is-the-primitive.md): if it
is a **place we keep**, it is a `Location`, and the shapes above describe
what feeds one rather than what replaces it.
