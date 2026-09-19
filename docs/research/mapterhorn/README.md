# Mapterhorn — would it add value? (2026-09-19)

**Verdict: yes, in exactly one place — as the DATA behind SNOW-693, replacing
Copernicus GLO-30 as source #2 on the grid SNOW-908 already built.** It changes
nothing about the architecture, and it should not. Every other use it suggests
(a hillshade layer, a serving-layer swap, replacing the Open-Meteo elevation
lookup) is either a different question or a worse trade.

---

## What Mapterhorn is

An open-source terrain pipeline and tileset: Copernicus GLO-30 as a global
baseline, refined with national high-resolution models where an open one
exists, published as Terrarium-encoded 512 px **lossless** WebP tiles and as
PMTiles archives.

| | |
|---|---|
| Encoding | Terrarium (`(R·256 + G + B/256) − 32768` metres, ~4 mm quantisation) |
| Container | Lossless WebP, 512 px; PMTiles archives for bulk |
| Pyramid | `planet.pmtiles` z0–12 global; z13–17 in per-region files named by their z6 tile (e.g. `6-33-22.pmtiles` = Interlaken) |
| Switzerland | swissALTI3D (0.5 m native) |
| Also refined | France, Austria, Italy, Germany, and ~17 other European countries from national models |
| Elsewhere | Copernicus GLO-30 (30 m) |
| Licence | Code BSD-3; data under each source's own open licence, attribution required |
| Distribution | `download.mapterhorn.com` + mirrors; ~9.8 TiB total, built from ~14.5 TiB of source |
| Hosted endpoint | `tiles.mapterhorn.com/{z}/{x}/{y}.webp` — real, but **hotlinking is discouraged**; the project asks you to copy the archives |
| Backing | NLnet-funded, Cloudflare R2/Workers sponsorship; komoot, Graphhopper and ORF.at self-host the archives |

## Why it matters here specifically

SNOW-693's whole reason for existing is that the slope surface stops at a
straight line through the Alps. swisstopo's `hangneigung-ueber_30` covers
Switzerland plus a buffer; the Zillertal, the Kitzbühel Alps, the Dolomites and
the Queyras fall outside it — **core ALBINA and Météo-France terrain we publish
bulletins for.** The same hole exists on the sampling side: the SNOW-908 grid is
Switzerland-only, so route slope colouring (SNOW-910), crux marking (SNOW-911)
and route scoring (SNOW-839) all return `OUTSIDE_COVERAGE` for two of our three
providers' ground.

The plan of record fills that hole with GLO-30 at 30 m. Mapterhorn fills the
same hole with the national LIDAR models — roughly 1 m native in France and
Austria — and falls back to GLO-30 only where nothing better is open.

| Ground | Today | SNOW-693 as scoped | With Mapterhorn |
|---|---|---|---|
| Switzerland | swissALTI3D, 2 m native → our 5 m grid | unchanged | unchanged (same source, already ours) |
| French Alps / Écrins / Queyras | nothing | GLO-30, 30 m | RGE ALTI, ~1 m |
| Tirol / Zillertal | nothing | GLO-30, 30 m | Austrian ALS, ~1 m |
| Dolomites / South Tyrol | nothing | GLO-30, 30 m | regional Italian LIDAR (verify per province) |
| Everywhere else | nothing | GLO-30, 30 m | GLO-30, 30 m — identical |

It is never worse than the plan of record and an order of magnitude better on
the terrain the ticket was actually raised for. That is the entire case.

## What it does NOT change

Mapterhorn is a **data acquisition and licence-normalisation shortcut**, not an
architecture. Sourcing RGE ALTI, Austrian ALS and Italian regional LIDAR
ourselves means four licence reviews and four heterogeneous formats; Mapterhorn
has already done that work and publishes a STAC catalogue of what it used.

Everything SNOW-908 settled stays settled:

- the 5 m EPSG:3035 grid, unmoved — an equal-area metric projection is what
  makes a slope kernel honest, and Web Mercator is not (its scale factor is
  `1/cos(lat)`, so a gradient computed on mercator pixels is wrong by ~45% at
  46° unless corrected);
- `grid.json` as the contract, the 204-outside-coverage rule, `TERRAIN_VERSION`;
- `apps/locations/services/terrain.py` and the unknown-is-a-reason rule —
  **unchanged, and it must not learn a second backend.**

So: **ingest at build time in `snowdesk-tiles`, not at read time in Django.**
Decode terrarium → warp to EPSG:3035 at 5 m → cut the same Int16 tiles. A new
first stage in `build-terrain.sh`, nothing downstream.

## Open questions before committing (in order of how much they could bite)

1. **Vertical datum.** GLO-30 is EGM2008 orthometric, swissALTI3D is LN02,
   RGE ALTI is NGF-IGN69. If Mapterhorn does not normalise these, a seam
   between two sources carries a step — up to tens of metres in the Alps.
   Slope over a 10 m window barely notices a smooth offset; a **step at a seam**
   would render as a cliff that isn't there, and displayed heights would
   disagree across a border. Verify before building, the same way SNOW-908 made
   the licence a stop condition.
2. **Provenance granularity.** `TerrainSource` records one `quality` tier and one
   `native_resolution_m` per entry, deliberately. One registry entry called
   "mapterhorn" would claim 0.5 m LIDAR over ground that is 30 m radar — exactly
   the false-accuracy claim the registry's native-vs-cell-size split exists to
   prevent. Derive **one entry per underlying national model** from Mapterhorn's
   STAC catalogue, with its own coverage box and tier.
3. **Double resampling.** Mapterhorn tiles are already resampled onto a mercator
   pyramid; warping them to EPSG:3035 is a second generation. At z14 a pixel is
   ~3.3 m at 46°, comfortably under our 5 m cell, so this is acceptable — but
   it is a real (small) loss against sourcing the national DEMs natively.
   z15 (~1.7 m) removes the concern at 4× the bytes.
4. **Volume.** An Alps-wide bbox (~5–16.5 E, 43–48 N) is roughly 210k tiles at
   z13–14 — tens of GB to pull once. Output is ~30–40 GB of Int16 tiles in R2,
   under a dollar a month, and egress is free. Not a blocker; it is a build-box
   disk figure, in the same class as SNOW-908's 100 GB.
5. **Attribution.** Multi-source, each with its own wording. `grid.json` already
   carries attribution on the source entry so the credit travels with the data —
   the pattern holds, it just gets more rows, and anything surfacing a height or
   a slope has to show them.

## Things it suggests that we should NOT do

- **Sample `tiles.mapterhorn.com` from Django at read time.** A second backend, a
  WebP decode, a mercator slope correction, and a dependency on a community
  service whose own docs discourage hotlinking — to avoid a build stage we
  already run.
- **Add a Mapterhorn hillshade / 3D terrain layer to the map.** Possibly a good
  idea, genuinely a different ticket, and it is not free: a raster terrain
  source costs one more tile per cell per source on every offline area download
  (`sourceScaledMb`). swisstopo's winter style already carries relief inside
  Switzerland. Decide it on its own merits, not as a rider.
- **Replace the Open-Meteo elevation lookup.** One caller left
  (`apps/favourites/services`), it works, and our grid answers only the Alps.
  A side benefit if the coverage lands, never a reason.
- **Backfill missing GPX `<ele>` from the DEM.** `elevation_profile_core.js`
  refuses to invent elevation on stated safety grounds. That decision is not
  about data availability and a better DEM does not reopen it.

## Recommended next step

Amend SNOW-693 in place: same shape ("add a source onto a grid that already
exists"), swap the dataset from GLO-30 to Mapterhorn-derived national models,
and add the datum check as a stop condition before any download — the same
discipline SNOW-908 applied to the licence. A Zermatt-sized bbox already runs
the whole pipeline in 22 seconds; the honest first move is a Zillertal-sized one
against known ground on both sides of a seam.

## Verification gaps

`mapterhorn.com`, `protomaps.com`, `oliverwipfli.ch`, `source.coop` and
`spatialists.ch` all returned `EGRESS_BLOCKED` from this session, so the figures
above are **search-corroborated, not read from the primary source** — in
particular the country list, the 9.8 TiB total and the per-country native
resolutions. Domains recorded in
[`docs/environment-network-allowlist.md`](../../environment-network-allowlist.md).
Confirm from `mapterhorn.com/attribution` and `/data-access/` before the ticket
is scoped.
