---
name: terrain-unknown-is-a-reason-not-a-null
description: sample_height and sample_slope return a TerrainUnknown — outside_coverage, no_data, unavailable — never None; a 204 is the coverage answer
status: current
last-reviewed: 2026-09-13
---

# A terrain unknown is a reason, never a null

## Decision

`sample_height` and `sample_slope` (`apps/locations/services/terrain.py`)
return a `TerrainHeight` or `TerrainSlope` carrying **either** a figure and
the `TerrainSource` it came from **or** one of three named reasons:
`OUTSIDE_COVERAGE`, `NO_DATA`, `UNAVAILABLE`. There is no code path
returning a bare `None`, and `__post_init__` refuses a result that is both
or neither.

Three rules follow from it:

- **There is no committed copy of `grid.json`.** It is fetched from
  `TERRAIN_TILE_BASE_URL` and cached for an hour. When it cannot be read,
  callers get `UNAVAILABLE`.
- **The coverage bbox is deliberately crude**, and the origin's
  `204 No Content` is the real answer.
- **`window_m` is the spacing between the sampled cells**, not the width of
  the patch.

## Why

Roughly half the grid's rectangle is ground no source covers — swissALTI3D
fills 27,331 of the 53,760 tile slots its bbox spans, because Switzerland
is a diagonal country in an axis-aligned box. Every consumer of this
service paints its answer: SNOW-910 colours a route line by steepness,
SNOW-911 marks its cruxes, SNOW-839 scores it against the bulletin. A
`None` reaching any of them is one `or 0` away from a green line over
unsurveyed ground, and the reader has no way to tell that apart from a
measured 0 degrees on a valley floor. The two must be different values, and
a named reason is the only shape that cannot be coerced into a number by
accident. The reasons stay distinct from each other because they call for
different responses: `OUTSIDE_COVERAGE` is permanent, `UNAVAILABLE` is ours
and transient.

A committed fallback `grid.json` would look like resilience and behave like
a trap. The geometry it states — cell size, tile size, height scale, row
order — is what every sample is addressed by, so a rebuilt tileset read
through a stale definition does not fail: it returns heights for the wrong
cells, at the wrong scale, plausibly. `UNAVAILABLE` for an hour is a better
outcome than a season of wrong answers nobody notices.

Making the coverage predicate exact would mean shipping the country's
outline and testing a polygon per sample, to save a request whose answer is
already memoised for the life of the process. The bbox is therefore a
superset — a `True` means "worth asking" — and the 204 decides. A 204 is
memoised; a timeout is not, so an outage cannot cache itself as
"unsurveyed".

`window_m` as spacing was chosen with the user rather than inherited. At
the default 10 m the kernel reads cells 10 m apart and spans 30 m of
ground. 10 m spacing is what SLF and swisstopo compute the 30 / 35 / 40
degree classes at, so our angles agree with the slope overlay painted
beside them on the same map. Reading it as a patch width would give a 3.3 m
spacing, a rougher answer, and two numbers on one screen disagreeing about
one hillside.

## Consequences

- Every caller branches on `is_known` or on `unknown`, and a surface that
  cannot render an unknown must say so rather than default to zero.
- A `None` creeping back into either return type is a regression with a
  test against it, not a style preference.
- `native_resolution_m` (2 m, surveyed) and `cell_size_m` (5 m, stored) are
  separate claims and must not be printed as one.
- SNOW-693 adds coarser sources outside Switzerland by appending to
  `sources[]`; the tier rule in `terrain_sources.select_source` already
  decides which one answers where two rectangles overlap.
- Operational detail for the tileset itself lives in
  [`../runbooks/terrain-tileset.md`](../runbooks/terrain-tileset.md).
