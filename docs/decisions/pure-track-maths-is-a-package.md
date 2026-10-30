---
name: pure-track-maths-is-a-package
description: track/ pure package (no Django, no apps.*); render fp and same_render; SourceSkiTour render fields; render_ski_tours
status: current
last-reviewed: 2026-10-30
---

# Pure track maths is a package, and the service renders SAC tours

## Decision

**The maths that turns a line into a drawing is `track/`, a root-level
package that imports neither Django nor any `apps.*` package.** It holds
the geo helpers (`track.geo`, formerly `apps/core/geo.py`), the terrain
reader (`terrain`, `terrain_grid`, `terrain_sources`), the walk
(`slope_samples`), legs, bank, fall line, passages, the slope and terrain
wires, the render builder (`track.render`) and SAC tour ordering
(`track.sac`). Callers pass in what Django used to supply: the tile base
URL to `load_grid`, the grid to `build_slope_samples`, the summits and the
sampler to `walk_tour`. Nothing re-exports the old paths.

**A SAC tour is walked and drawn once, by the reference-data service.**
`SourceSkiTour` holds `slope_samples`, `track_render` and
`render_input_fp`; `render_ski_tours` (daily, 05:30 UTC) fills them; the
v2 `ski-tours` feed serves them; every instance copies them into
`SkiTour`, both or neither.

**Rendering twice is a no-op.** A render carries `fp`, the hash of
everything `build_track_render` reads (`render_fingerprint`); a writer
that would store an equal `fp` writes nothing (`same_render`, in
`store_terrain` and `render_source_tour`). A tour's walk is keyed by
`render_input_fp`, the document's `content_hash` plus the terrain grid's
identity, so it is walked again only when either changes.

## Why

Hugo, 2026-10-30: a render that is deterministic from external data is
calculated as soon as possible and kept with that data, but not at the
price of an import into `apps/ingest`; an expensive one is calculated
offline, and calculating it twice is a no-op. And code that is pure and
shared is its own package, root-level for now.

The walk is shared by an instance (a user's upload) and the service (an
SAC tour). Leaving it in `apps/routes` would mean ingest importing an app,
which [ingestion-is-a-rehostable-unit](ingestion-is-a-rehostable-unit.md)
forbids. Moving it under `apps/ingest` would put a user's upload behind
the ingest facade. A third, pure package serves both and is what a
rehosted service would take with it.

## Consequences

- `tests/track/test_boundary.py` fails a `django`, `apps` or `config`
  import anywhere under `track/`.
- `load_grid` memoises a grid in-process for an hour, not in Django's
  cache; `tests/conftest.py` clears it per test.
- An instance no longer walks SAC tours in its nightly reconcile; a tour
  the feed has not drawn yet still falls back to the panel's on-open walk.
- An outage stores nothing (the tour is retried); a line too short to walk
  is stored as no terrain and settled.
- `/api/data/v2/ski-tours/` items carry `slope_samples` and
  `track_render` beside `document`.
