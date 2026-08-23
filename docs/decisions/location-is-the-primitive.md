---
name: location-is-the-primitive
description: Location is the domain primitive every place FKs to — resorts, favourites, observations; ForecastCell is a quantised fetch cell
status: current
last-reviewed: 2026-08-21
---

# Location is the primitive — supersedes location-first-information-model

**Supersedes
[`location-first-information-model`](location-first-information-model.md),**
accepted and corrected the same day. That document's tiers, weather-sourcing
rule and point-versus-extent argument are carried forward here; its §4–5 —
`NamedLocation` as a curated table beside an anonymous `ForecastPoint`, with
identity opt-in — inverted the model and are withdrawn.

## Decision

**A location is a point on the map, and it is the locus of the model.**
Everything that is *somewhere* reaches a `Location`: a resort, its
mid-station and its peak, a saved favourite, a field observation, a region's
centroid. One table:

```
Location                          apps/locations/
  latitude, longitude   exact WGS-84 — immovable
  elevation_m           looked up once via fetch_elevation
  name                  nullable; a curated place has one
  kind                  nullable; VILLAGE | MID | PEAK
  forecast_cell → weather.ForecastCell (PROTECT)
```

There is no separate "curated place" model. A curated place is a `Location`
that has a `name`, so Mont Fort is one row referenced by Verbier, Nendaz,
Veysonnaz and Thyon — the sharing falls out of the model rather than needing
a table to express it.

**`ForecastPoint` is a fetch cell, not a domain entity**, and was renamed
`ForecastCell` (SNOW-703) to say so. It is the quantised cell at which we call
Open-Meteo — many `Location`s to one cell, weather rows hanging off the cell.
The forecast is *for a location*; the cell is only how we avoid paying twice
for two pins 300 m apart.

**A row exists for a place we keep.** A transient coordinate — a live GPS
fix, a GPX trackpoint — is *resolved against* locations without minting one.
`Route.points` stays a JSONField of simplified trackpoints; those are
geometry, not places. "Everything is a location" means every **place**, not
every **coordinate**.

## Why

**The arrow was backwards.** The superseded document had a curated place
point at an anonymous weather row, and justified the split on privacy: an
anonymous cell "lets one cell serve a public resort and a private pin without
leaking either". The code does not support that claim.

- `Favourite` stores the exact pin — `latitude` / `longitude` are plain
  floats holding "WGS-84 latitude of the saved pin"
  (`apps/favourites/models.py`). Quantising the row a favourite *references*
  protects nothing when the precise coordinate is in the favourite itself.
- [`forecast-point-quantisation`](forecast-point-quantisation.md) never
  mentions privacy. Its entire rationale is grid geometry — 0.01° latitude
  ≈ 1.1 km, a 200 m band matching "the resolution at which mountain weather
  meaningfully differs" — plus reuse-first to avoid "needlessly fragmenting
  nearby pins into separate forecast fetches". Cost and forecast resolution.
- `FieldObservation` is provenance, not anonymisation: the module notes that
  any offset between `gps_latitude` and `latitude` "is recoverable from the
  difference between these". `location_source` and `accuracy_radius_km`
  record *how a coordinate was obtained and how far to trust it* — which is
  data about the report, and stays on the report.

Quantisation is a cost mechanism that was mistaken for a privacy boundary,
and a model was built on the mistake.

**What carries forward unchanged.** The three tiers (Map → Region →
Location, with the published bulletin beside them as provenance); the
weather-sourcing rule (numbers from points, decoration from
`WeatherSnapshot`, no model pin); and the point-versus-extent argument — *a
location is a point, a resort has several*, and elevation extent is a
property of the **set**, not of any location in it. That last one is the
thesis; the withdrawn sections should have been derived from it.

## Consequences

- **`apps/locations/` is a new app.** `regions`, `weather`, `favourites`
  and `observations` all reference `Location`; housing it in `regions` would
  make four apps depend on `regions` and worsen the existing `regions` ↔
  `weather` mutual FK. `core/` is wrong too — it holds abstract bases and
  HTTP-layer infra (`RequestLog`, `IdempotencyRecord`), not domain tables.
  `apps/core/coordinates.py` stays put; it is validation, not domain.
- **The `ForecastPoint` → `ForecastCell` rename pins `Meta.db_table`**, per
  the [SNOW-654 playbook](weather-is-its-own-app.md): code moves, the
  database does not, and `sqlmigrate` prints a no-op.
- **Backfills are management commands, not migrations.** Minting a
  `Location` per existing `Favourite` and `FieldObservation` is a bulk data
  update, which migrations may not carry; schema migrations hold DDL only
  and the backfill is a `--commit`-gated command per the command contract.
- **Favourites are user data.** `Favourite.latitude/longitude/elevation`
  migrate onto `Location` with the FK repointed; the columns drop only once
  nothing reads them, in a later ticket.
- **Observations do not collapse.** Several observations sharing one
  `Location` is correct — the superseded objection was to an observation
  *being* a place, which an FK does not make it.
- **`ForecastCell.active()` sequences across tickets.** It gains a
  `Location` referent and loses `Favourite` / `Resort` as those migrate; the
  removals come last, or cells fall out of `active()` and
  `prune_forecast_points` deletes their stored weather.
- **`Location` and `ForecastCell` need `docs/glossary.md` entries.**
- **[`resort-page-shows-point-forecast`](resort-page-shows-point-forecast.md)
  will be superseded**, not by this decision but by the resort-page ticket
  that implements it: that page currently shows a region-centroid header
  directly above point values for the same day, which the weather-sourcing
  rule forbids.
- **Open questions carried forward** from the superseded document: whether
  the bulletin document gets a URL of its own, and whether the Region page
  keeps a danger hero. "Is a live GPS position a Location" is now answered —
  it resolves against locations, it does not mint one.
