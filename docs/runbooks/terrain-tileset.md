---
name: terrain-tileset
description: Terrain elevation tileset — swissALTI3D to a 5 m EPSG:3035 Int16 grid at /terrain/v1/, the build, grid.json, the 204 rule, TERRAIN_VERSION
status: current
last-reviewed: 2026-09-13
---

# Runbook — terrain elevation tileset (SNOW-908)

## What this is, and what it is not

A second tileset on the **same origin, the same R2 bucket and the same
Cloudflare Worker** as the basemap — a different artefact, not a different
system. Everything in
[`self-hosted-tiles.md`](self-hosted-tiles.md) about the hostname, the bucket
and the Worker applies unchanged.

**Nothing renders it.** The basemap exists to be painted; this exists to be
*read*. Django fetches a tile and decodes Int16 heights out of it to answer
"what is the height at this coordinate, and therefore what is the slope
angle" — the question SNOW-910 (colouring a route line by the slope it
crosses), SNOW-911 (crux marking) and SNOW-839 (scoring a route against the
bulletin) all need, and the one a pre-rendered raster cannot answer. MapLibre
can paint the slope overlay; nothing can read a value back out of it.

So the two terrain surfaces are unrelated in everything but subject:

| | Slope angle overlay | This tileset |
|---|---|---|
| What it is | swisstopo's pre-rendered `ch.swisstopo.hangneigung-ueber_30` raster | Our own Int16 height grid |
| Who consumes it | MapLibre, in the browser | Django, server-side |
| Answers | "shade this pixel" | "this coordinate is 2,417.25 m" |
| Lives in | `static/js/slope_overlay_core.js` | SNOW-917 (not yet started) |

## Where the build lives

The build, the grid definition and the publish path are in
**[snowdesk-tiles](https://github.com/hugorodgerbrown/snowdesk-tiles)** on
`main`, alongside the basemap pipeline:

| File | What it is |
|------|------------|
| `scripts/terrain_grid.py` | The grid geometry and encoding. **The only copy**, and the contract with SNOW-917. |
| `scripts/fetch_swissalti3d.py` | Lists and downloads the source squares from swisstopo's STAC API. |
| `scripts/cut_terrain_tiles.py` | Cuts the warped raster into skirted Int16 tiles, and writes `grid.json`. |
| `scripts/build-terrain.sh` | The five stages end to end, into `dist/terrain/`. |
| `scripts/vm-build.sh terrain` | The whole VM path — installs GDAL, builds, publishes. |
| `scripts/verify.sh` | Acceptance checks against the live origin. |

Nothing in *this* repo builds or serves it. What this repo will own is the
sampling side — SNOW-917, not yet started.

## The grid, as published on 2026-09-13

The geometry is deliberately **not** configurable in the build script. It lives
in `terrain_grid.py` because it is a contract, not a tunable, and it is
published in `grid.json` so the Django side reads it rather than hardcoding it.

| | Value | Why |
|---|---|---|
| Grid id | `snowdesk-terrain-5m-3035` | |
| Projection | EPSG:3035 (ETRS89-LAEA) | Equal-area and metric across the whole Alps, so a second source can be added without moving the grid. |
| Cell spacing | 5 m | Storage, not analysis — heights are stored, so the analysis window is a read-time choice. Default window is 10 m, matching what swisstopo compute their 30° raster at. |
| Tile | 256 × 256 cells (1,280 m) | One point sample pulls 133 kB, not half a megabyte. |
| Skirt | 1 cell each side → 258 × 258 stored | So a sample in the outermost data cell can read its own neighbours. |
| Tile size | 133,128 bytes | 258 × 258 × 2, exactly, for every tile that exists. |
| Stored value | little-endian Int16, `height_m / 0.25` | 0.25 m keeps quantisation noise below swissALTI3D's own vertical accuracy; whole metres would put ±1.0° of noise on a 10 m window, against thresholds that are 5° apart. |
| Height offset | 0 | |
| Nodata | `-32768` | Distinct from every representable height, including 0 m. |
| Row order | north to south | A plain north-up raster, as GDAL writes one. |
| Column order | west to east | |
| Boundary rule | half-open `[south, north)`, `[west, east)` | **Not GDAL's rule** — a coordinate on a boundary belongs to the cell north and east of it. Only bites on exact multiples of 5 m, and moves the answer by one cell. |
| Grid origin | easting 0, northing 0 | Tile indices are east/north from the CRS origin, so both are positive everywhere in Europe. |

The 2026-09-13 build produced a **71,680 × 49,152 cell** grid — 53,760 tile
slots, of which **27,331 hold data, totalling 3.5 GB**. The rest are empty
because Switzerland is diagonal in a rectangular grid.

## The R2 prefix and the Worker route

Objects live under the **`terrain/` prefix** in the existing bucket:
`terrain/{x}/{y}.s16`, plus `terrain/grid.json`. The Worker serves them at:

| URL | Serves | Cache-Control |
|-----|--------|---------------|
| `https://tiles.snowdesk-data.info/terrain/v1/{x}/{y}.s16` | one Int16 tile | `max-age=31536000, immutable` |
| `https://tiles.snowdesk-data.info/terrain/v1/grid.json` | the definition above | `max-age=3600` |

**The version segment is stripped by the Worker.** Objects are stored
unversioned, so a rebuild replaces them in place and no client is left holding
a URL that 404s. The segment never reaches the bucket — it exists to vary the
cache key, not to address objects — which is why bumping it and rebuilding are
two halves of one action (see below).

The segment is also **not validated**: the Worker's route matches any segment,
so `/terrain/banana/3200/2000.s16` returns the same tile as `/terrain/v1/…`,
and `/terrain/v99/grid.json` returns whatever definition is current. That is
worth knowing before reading the version as an isolation guarantee, because it
is a weaker one than it looks — see
[below](#what-the-version-bump-does-not-buy).

### Outside coverage is 204, never 404 and never a height

Most of the grid's rectangle is legitimately ground no source covers. The
Worker answers **204 No Content** there — an ordinary, cacheable answer — and
`grid.json` states the rule in its own `absent_tile` field so it travels with
the data:

> 204 No Content means no source covers this tile. It is not an error and must
> never be read as level terrain.

**This is load-bearing.** SNOW-839 and SNOW-910 both turn on an absent answer
never rendering as gentle ground; a 404 read as "no slope here" is the failure
mode the distinction exists to prevent. A **negative tile index is 404** —
that is a caller's arithmetic bug, and 404 says so where 204 would look like
empty ground.

Verified live on 2026-09-13:

```
/terrain/v1/3200/2000.s16   200, 133128 bytes, immutable
/terrain/v1/3131/1963.s16   204   (inside the rectangle, outside coverage)
/terrain/v1/-1/2000.s16     404   (negative index)
```

## Licence and attribution

SNOW-908 made the licence a stop condition, checked **before anything was
downloaded**, and it passes.

swisstopo have published all federal geodata under their responsibility as
**Open Government Data since 1 March 2021**: it may be used, distributed and
made accessible, enriched and processed, and used commercially. geocat's
metadata record for swissALTI3D states the constraint as *"Opendata BY: Open
use. Must provide the source."* No authorisation is needed, so redistributing a
derived, resampled, requantised tileset is permitted.

The one obligation is attribution. swisstopo accept `Source: Federal Office of
Topography swisstopo` or `© swisstopo`; the short form is what
`terrain_grid.py` publishes, **on the source entry in `grid.json`**, so the
credit travels with the data rather than being remembered separately. Anything
surfacing a height or a slope derived from it has to show it.

This is **not** a Creative Commons dataset even though the obligation looks
like one — swisstopo state CC licences are incompatible with GeoIG/GeoIV and
deliberately do not use them. Do not label it CC-BY.

- [Terms of use for free geodata and geoservices (OGD)](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices)
- [swissALTI3D](https://www.swisstopo.admin.ch/en/height-model-swissalti3d)

## Building it

Needs GDAL and Python 3.12+. Budget **~100 GB of disk and about 90 minutes**;
peak was 56 GB of intermediates in `work/terrain` alongside 3.5 GB of tiles.
It wants disk and cores, not planetiler's 16 GB of RAM.

On a throwaway VM that is one command, which installs GDAL, prompts for the R2
credentials and publishes when the build finishes:

```bash
./scripts/vm-build.sh terrain
```

Measured on 2026-09-13 on a Hetzner CX42 (8 vCPU): roughly 20 minutes to page
the catalogue, 20 to download at eight parallel curls, and the warp is the
longest single stage. Destroy the VM afterwards — the R2 credentials are in its
memory, and deleting the box is the cheapest rotation there is.

By hand, the same five stages, each skipped if its output is already there so
an interrupted run resumes (`FORCE_TERRAIN=1` redoes the GDAL stages):

```bash
./scripts/build-terrain.sh
op run --env-file=.env.1password -- ./scripts/upload.sh
./scripts/verify.sh
```

1. List the swissALTI3D squares over `TERRAIN_BBOX` from swisstopo's STAC API,
   taking the **2 m** GeoTIFF, one item per square kilometre, newest survey per
   square. The 2026-09-13 run listed **80,485 items and dropped 36,835 as
   superseded, leaving 43,650 squares** surveyed between **2019 and 2025** —
   swisstopo re-survey on a six-year cycle, so newest-per-square makes the grid
   a deliberate patchwork of vintages rather than an accidental one.
2. Download them — ~44 GB, and the long pole by a distance.
3. `gdalwarp` to EPSG:3035 at 5 m with `-r average`. Averaging from the 2 m
   source is the whole reason for not taking a coarser one.
4. `gdal_translate` to a flat Int16 ENVI raster, quantised onto the stored
   scale in one exact linear step. The `-scale` endpoints come from
   `terrain_grid.py`, so the encoding cannot drift from what SNOW-917 decodes
   with.
5. Cut tiles. Pre-quantised and tile-aligned, so this is pure byte slicing.

`upload.sh` publishes only what is staged in `dist/` — `build-terrain.sh`
stages `dist/terrain/` and nothing else — so the box that ran the build can
publish the tileset without mirroring the unchanged basemap assets. `grid.json`
goes last, with the one-hour TTL.

### The bbox, and iterating cheaply

The full build covers `TERRAIN_BBOX="5.95 45.72 10.50 47.83"`. Override it to
build one region — a 66 km² box around Zermatt runs end to end in **22
seconds**, which makes it a cheap way to prove the whole pipeline before
committing to the full download:

```bash
TERRAIN_BBOX="7.70 45.98 7.80 46.05" ./scripts/build-terrain.sh
```

That box is the useful smoke test because the answers are checkable: Zermatt
village reads 1608.5 m against a published 1,608 m, the Gornergrat ridge
3124 m, the whole grid spans 1459.5–3391.5 m. One number landing on the village
pins the projection, the tile and cell addressing, the row order and the height
scale at once — each of which can individually return a plausible height for
every coordinate while being wrong.

`verify.sh` does the same thing against the live origin: it compares the
published `grid.json` against `python3 scripts/terrain_grid.py definition`,
reads heights at named places and asserts them against known ground (two lake
surfaces, then the range of the country from Basel to the Jungfraujoch), and
confirms the 204 and 404 behaviours above. A grid that is offset, flipped
north-south or built in the wrong projection still returns a plausible height
everywhere; comparing against known ground is the only thing that catches it.

`TERRAIN=0 ./scripts/verify.sh` skips the section. That is for shipping a
basemap-only change before the terrain build has ever run — not a way past a
failure.

## Re-running it when a parameter changes

There is **no schedule and nothing to keep alive**. The only two things that
would ever trigger a re-run are swisstopo's six-yearly re-survey and a change
to our own parameters, and both are years apart. That is why the build is a
committed script rather than a sequence someone performed once: a re-run should
be a diff, not an archaeology exercise.

**Bump `TERRAIN_VERSION` for any change to the grid definition, not just to the
heights.** Tiles are cached immutable for a year *by URL*, so a client holding
tiles cut on one geometry and decoding them under another gets nonsense — no
error, just wrong heights. The two halves go together:

- Rebuilding **without** bumping leaves year-long caches serving the old bytes.
- Bumping **without** rebuilding hands out fresh URLs for the same objects,
  because the Worker strips the segment.

So the sequence for a geometry change is: edit `terrain_grid.py`, bump
`TERRAIN_VERSION` in `scripts/config.sh`, rebuild, upload, `verify.sh`. The
Worker needs no deploy — it never reads the segment.

Re-surveyed heights with the geometry unchanged need no bump in principle, but
the year-long TTL means clients keep the old tiles until they fall out of
cache. Bump it anyway if the correction matters.

### What the version bump does not buy

The bump gives the **new** geometry a clean URL space. It does **not** stop the
**old** one from serving the new bytes, and that asymmetry is the part to hold
on to.

Because the segment is stripped, `/terrain/v1/{x}/{y}.s16` and
`/terrain/v2/{x}/{y}.s16` are the same object. After a geometry-changing
rebuild, a v1 request served from a warm edge or client cache returns the old
bytes, and a v1 request that misses returns the **new** bytes — decoded by a
consumer still holding the v1 numbers. Within one route calculation a consumer
can get a mix of both, which is the silently-wrong-height failure this whole
design is arranged to prevent.

What bounds it is `grid.json`, not the version. It is unversioned too, so it
always answers with the current definition — including the current `version`
and `tile_url_template` — under a **one-hour TTL**. A consumer that re-reads it
moves to the new URL space within the hour. So the exposure is: one hour from
publish, for a consumer that reads tiles without re-reading `grid.json` first.

Two ways to close it properly, neither yet done, both in `snowdesk-tiles`:

1. **Key the objects by version** — store `terrain/{version}/{x}/{y}.s16` so an
   old URL keeps resolving to the geometry it was cut for. Costs one extra copy
   during the transition, about another five cents a month at the current size.
2. **Gate the segment in the Worker** — serve only the current version and
   answer anything older with an error rather than a height. Needs the Worker
   to know the current version, so it becomes a deploy.

Until one of them lands, the operational rule is: **re-read `grid.json` before
sampling after any publish**, and treat the hour after a geometry change as a
window in which sampled heights are not trustworthy. Raised by review on
[#922](https://github.com/hugorodgerbrown/snowdesk-data-pipeline/pull/922).

## Coverage, and the accepted limitation

**Switzerland only.** The grid is Alps-wide from day one with only the Swiss
part populated — adding a source later (SNOW-693, Copernicus GLO-30) is
"resample a coarser source onto the existing grid"; the grid itself never
moves. A source is a registry entry in `terrain_grid.py` carrying coverage,
quality tier, native resolution, licence and attribution, and **native
resolution is recorded separately from cell spacing** because they are not the
same claim: a 30 m source on a 5 m grid is upsampling, which is honest only so
long as nothing downstream reads 5 m cells as 5 m of information.

The accepted limitation, stated plainly: **the display overlay covers more
ground than the sampling grid**. The slope raster's coverage rectangle reaches
into France and Austria; this grid holds heights only for Switzerland. In the
Vanoise or the Écrins a user will see slope shading under an uncoloured route
line. That is not a correctness bug so long as unknown never renders as gentle
ground — which is what the 204 rule guarantees — and it is the strongest
argument for GLO-30 as source number two.

## Cost

Storage is the only ongoing cost, and it is the reason the grid can be this
fine. **~3.5 GB in R2, about five cents a month.** Egress is free, the writes
are one-off, and there is no dyno and no schedule.

## What this repo will own

**SNOW-917** — the sampling API, the source registry and `TERRAIN_TILE_URL` —
and it is **not yet started**. The one rule it carries in from here: it reads
`grid.json` rather than hardcoding the geometry. A grid rebuilt with different
numbers and a sampler still applying the old ones does not fail loudly; it
returns plausible, silently wrong heights. `verify.sh` compares the published
definition against the source on every run for the same reason.
