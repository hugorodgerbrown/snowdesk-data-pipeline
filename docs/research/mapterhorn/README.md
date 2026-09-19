# Mapterhorn — would it add value? (2026-09-19)

**Verdict: yes, in exactly one place — as the DATA behind SNOW-693, replacing
Copernicus GLO-30 as source #2 on the grid SNOW-908 already built.** It changes
nothing about the architecture, and it should not. Every other use it suggests
(a hillshade layer, a serving-layer swap, replacing the Open-Meteo elevation
lookup) is either a different question or a worse trade.

## What Mapterhorn is

An open-source terrain pipeline and tileset: Copernicus GLO-30 as a global
baseline, refined with national high-resolution models wherever an open one
exists, published as Terrarium-encoded 512 px **lossless** WebP tiles and as
PMTiles archives.

| | |
|---|---|
| Encoding | Terrarium (`(R·256 + G + B/256) − 32768` metres), lossless WebP, 512 px |
| Pyramid | `planet.pmtiles` z0–12 global; z13–17 in per-region files named by their z6 tile |
| Elsewhere | Copernicus GLO-30 (30 m) |
| Licence | Code BSD-3; data under each source's own open licence, attribution required |
| Distribution | `download.mapterhorn.com` + mirrors (`mirrors.json`, `mirrorstatus.json`); extracts via the `pmtiles` CLI against a bbox |
| Hosted endpoint | `tiles.mapterhorn.com/{z}/{x}/{y}.webp` + `tilejson.json` — real, but the project asks you to copy the archives rather than hotlink |
| Backing | NLnet-funded, Cloudflare R2/Workers/bandwidth; komoot, Graphhopper and ORF.at self-host |

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
same hole with the open national models. Every row below is a catalogue entry
read from `source-catalog/<id>/metadata.json`, not an inference:

| Ground | SNOW-693 as scoped | Mapterhorn entry | Native | Licence |
|---|---|---|---|---|
| Tirol / Zillertal (AT-07) | GLO-30, 30 m | `at1` — BEV ALS-DGM Höhenraster | **1 m** | CC-BY-4.0 |
| French Alps / Écrins / Queyras | GLO-30, 30 m | `frrgealti1metro` — IGN RGE ALTI® | **1 m** | Licence Ouverte 2.0 |
| South Tyrol / Dolomites | GLO-30, 30 m | `itbozen` — Provincia di Bolzano DGM | **2.5 m** | CC0 |
| Trentino / Brenta | GLO-30, 30 m | `ittrentino` — LiDAR PAT 2014+2018 | **5 m** | CC BY 2.5 |
| Aosta, Lombardia, Piemonte | GLO-30, 30 m | `itaosta`, `itlombardia`, `itpiemonte` | not yet read | — |
| Bavarian / Allgäu Alps | GLO-30, 30 m | `debayern` | not yet read | — |
| Everywhere else | GLO-30, 30 m | `glo30` | 30 m | identical |

It is never worse than the plan of record and 6–30× better on the terrain the
ticket was actually raised for. That is the entire case.

**The catalogue refuses share-alike and non-commercial sources by policy**
(`source-catalog/README.md`), so every entry is redistribution-safe by
construction — the same stop condition SNOW-908 applied by hand, applied
upstream. The obligation is attribution, per source, which is what
`grid.json`'s source entries already carry.

## What it does NOT change

Mapterhorn is a **data acquisition and licence-normalisation shortcut**, not an
architecture. Sourcing RGE ALTI, BEV ALS-DGM and four Italian provincial models
ourselves means six licence reviews and six formats; Mapterhorn has done that,
and publishes the result as a catalogue of one folder per source carrying
`{name, website, license, producer, resolution, access_year}` plus the original
licence PDF — **a near 1:1 mapping onto our own `TerrainSource`.**

Everything SNOW-908 settled stays settled:

- the 5 m EPSG:3035 grid, unmoved — an equal-area metric projection is what
  makes a slope kernel honest, and Web Mercator (which is what Mapterhorn
  publishes in) is not: its scale factor is `1/cos(lat)`, so a gradient computed
  on mercator pixels is wrong by ~45% at 46° unless corrected;
- `grid.json` as the contract, the 204-outside-coverage rule, `TERRAIN_VERSION`;
- `apps/locations/services/terrain.py` and the unknown-is-a-reason rule —
  **unchanged, and it must not learn a second backend.**

So: **ingest at build time in `snowdesk-tiles`, not at read time in Django.**
Decode terrarium → warp to EPSG:3035 at 5 m → cut the same Int16 tiles. A new
first stage in `build-terrain.sh`, nothing downstream. The same decoded heights
are also what an Alps-wide **rendered** slope raster would be derived from — see
the correction under "Recommended next step": that raster is SNOW-693's actual
deliverable, and it rides on this ingest rather than competing with it.

## The two questions that decide it — both now answered from the pipeline source

**1. Vertical precision is zoom-dependent, and it is fine at the zoom we want.**
`utils.get_rounded_elevation_data` quantises before encoding:
`factor = 2**(19 - z) / 256`, capped at 1 m. So the tiles carry

| z | vertical step | horizontal at 46° |
|---|---|---|
| 15 | 0.0625 m | 1.7 m |
| **14** | **0.125 m** | **3.3 m** |
| 13 | 0.25 m | 6.6 m |
| 12 | 0.5 m | 13.3 m |
| ≤11 | 1 m (capped) | ≥26 m |

Terrarium's headline 1/256 m exists only at z19. **Pull z14**: 3.3 m horizontal
is comfortably under our 5 m cell, and 0.125 m vertical is *twice as fine as
what we store*. z13 lands exactly on our own 0.25 m step. SNOW-908 rejected
whole-metre quantisation because it puts ±1.0° of noise on a 10 m window —
z12 and below hit that, z14 does not, by a factor of eight.

**2. Vertical datums are not transformed at all.** `aggregation_reproject.py`
warps to `EPSG:3857` with `-r cubicspline` and does nothing vertical; the
catalogue's `metadata.json` records no vertical CRS field. Each source keeps its
own reference and the merge blends edges horizontally.

This is a smaller risk than it first looks, and the earlier draft of this
assessment overstated it. The tens-of-metres failure is ellipsoidal-vs-
orthometric mixing, and **none of the Alpine entries are ellipsoidal** — LN02,
NGF-IGN69, the Austrian and Italian national datums and GLO-30's EGM2008 are all
orthometric, mutually offset by decimetres. A ~0.5 m step across one 5 m cell is
~5.7° on a 10 m window: bounded, confined to the seam line itself, softened by
the edge blending, and it would render as a thin line one class too steep rather
than as a plausible wrong answer over an area. **Still spot-check it** with a
profile across a border before ingest — the catalogue does not record the datum,
so nothing upstream would catch a source that is ellipsoidal.

## Remaining open questions

1. **Double resampling.** Mapterhorn tiles are already on a mercator pyramid,
   cubic-spline resampled; warping them to EPSG:3035 is a second generation, and
   cubic spline can overshoot at cliff edges. At z14 this is acceptable; it is a
   real (small) loss against sourcing the national DEMs natively, which is the
   work Mapterhorn is saving us.
2. **Volume.** An Alps-wide bbox (~5–16.5 E, 43–48 N) is roughly 210k tiles at
   z13–14 — tens of GB pulled once, against ~30–40 GB of Int16 output in R2 at
   well under a dollar a month. A build-box disk figure, in the same class as
   SNOW-908's 100 GB.
3. **Which sources are actually live.** Switzerland appears in the catalogue as
   `debug-swissalti3d` (0.5 m) alongside `debug-glo30`, and that prefix reads
   like a test fixture rather than a production entry. Irrelevant to us — we hold
   swissALTI3D natively and keep it inside its box — but it means the catalogue
   is the list of *available* sources, not proof of what a given published tile
   contains. Confirm per source against `attribution.json` (or the coverage
   index) before relying on a resolution figure.
4. **Provenance in a MERGED mosaic — the one real design problem, and it is not
   solved by more registry rows.** Mapterhorn publishes a single merged surface:
   a tile holds national-model cells where one exists and GLO-30 cells where one
   does not, with no per-cell marker. Our registry is bbox-plus-tier —
   `select_source` returns the highest-ranked source whose **deliberately
   supersetted** rectangle contains the point — so a GLO-30 cell just outside the
   Austrian coverage but inside `at1`'s box would be reported as 1 m LIDAR.
   Worse, it breaks the rule that carries coverage today: **the 204 currently IS
   the coverage answer**, and a GLO-30 baseline means a tile exists everywhere,
   so nothing distinguishes "surveyed at 1 m" from "30 m radar upsampled onto a
   5 m grid". The superset box is safe today precisely because the origin's 204
   corrects it; against a merged source it has nothing correcting it.

   Three ways out, cheapest first:

   - **Clip on ingest to each model's own coverage polygon** and leave the rest
     204 — we hold only ground we can speak to, the existing semantics survive
     untouched, and the Alps gap closes for the terrain that has a national
     model. Mapterhorn already produces those polygons (`source_polygonize.py`,
     `create_coverage_index.py`, the per-source coverage GeoPackages), so this is
     ingest-time clipping, not geometry we invent.
   - **Store the source per cell** — a parallel byte grid alongside the heights,
     which makes provenance exact at the cost of a second artefact and a
     `grid.json` change.
   - **Hold exact (non-rectangular) coverage in the registry** and test the
     polygon per sample — honest, but it puts a point-in-polygon test on every
     sample, which is what the superset box was chosen to avoid.

   **Recommendation: clip.** Taking GLO-30's fill as well would trade a known
   unknown for an unmarked 30 m answer wearing a 1 m label, and that is the exact
   failure `native_resolution_m` and the 204 rule were both written to prevent.

## Things it suggests that we should NOT do

- **Sample `tiles.mapterhorn.com` from Django at read time.** A second backend, a
  WebP decode, a mercator slope correction, and a dependency on a community
  service whose own docs discourage hotlinking — to avoid a build stage we
  already run.
- **Add a Mapterhorn hillshade / 3D terrain layer to the map.** Possibly a good
  idea, genuinely a different ticket, and not free: a raster terrain source costs
  one more tile per cell per source on every offline area download
  (`sourceScaledMb`). swisstopo's winter style already carries relief inside
  Switzerland. Decide it on its own merits, not as a rider.
- **Replace the Open-Meteo elevation lookup.** One caller left
  (`apps/favourites/services`), it works, and our grid answers only the Alps.
  A side benefit if the coverage lands, never a reason.
- **Backfill missing GPX `<ele>` from the DEM.** `elevation_profile_core.js`
  refuses to invent elevation on stated safety grounds. That decision is not
  about data availability and a better DEM does not reopen it.

## Recommended next step

**First, a correction to the framing above: SNOW-693's user-visible deliverable
is the RENDERED slope raster, not the sampling grid.**
`docs/map-page-functional-spec.md` assigns that ticket the job of widening the
painted layer — today the menu row disables itself outside swisstopo's rectangle
because "inside it unshaded means under 30°; outside it unshaded means not
surveyed" — and `docs/runbooks/terrain-tileset.md` is explicit that the Int16
grid **is never rendered**. They are two surfaces with one subject.

So this must not be rescoped into "extend the sampling grid" and closed. What
Mapterhorn supplies is the **heights underneath both**: the same ingest feeds the
Int16 grid Django samples *and* the raster MapLibre paints, which is the argument
for doing them off one source rather than two. Either SNOW-693 keeps the rendered
raster as its deliverable and gains the grid extension, or the raster splits into
its own ticket — but it does not get amended away, or the Zillertal, the
Dolomites and the Queyras keep the disabled layer row this whole line of work
exists to remove.

With that fixed, the change to SNOW-693 is narrow: swap the dataset from GLO-30
to the Mapterhorn catalogue's national models pulled at z14, clip on ingest to
each model's coverage polygon (above), and keep the datum spot-check as a stop
condition before any bulk download — the discipline SNOW-908 applied to the
licence. A Zermatt-sized bbox already runs the whole pipeline in 22 seconds; the
honest first move is a Zillertal-sized one, verified against known ground on both
sides of the Austrian border.

## Verification notes

`mapterhorn.com`, `download.mapterhorn.com`, `protomaps.com`, `oliverwipfli.ch`,
`source.coop` and `spatialists.ch` all return `EGRESS_BLOCKED` from Claude Code
on the web (recorded in
[`docs/environment-network-allowlist.md`](../../environment-network-allowlist.md)),
so the attribution and data-access pages could not be read directly — **their
underlying data is served from `download.mapterhorn.com/attribution.json`, which
is blocked too.** Everything above that carries a number was instead read from
the project's own repository (`source-catalog/*/metadata.json`,
`source-catalog/README.md`, `pipelines/utils.py`,
`pipelines/aggregation_reproject.py`, `pipelines/README.md`), which is primary
source of a better kind for this purpose: it is what the build actually does.

What remains search-corroborated only, and should be confirmed before scoping:
the published tileset's total size (~9.8 TiB from ~14.5 TiB of source), the
hosted endpoint's usage figures, and which catalogue entries are live in the
current build.
