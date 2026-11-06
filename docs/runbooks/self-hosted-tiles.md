---
name: self-hosted-tiles
description: tiles.snowdesk-data.info — OPENFREEMAP_STYLE_URL cutover, z14 zoom-range trap, 3-D elevation copy /dem/<version>/ refresh and rollback
status: current
last-reviewed: 2026-11-06
---

# Runbook — self-hosted basemap origin (tiles.snowdesk-data.info)

## Where the origin lives

The tile origin — the planetiler build, the Cloudflare Worker, the style-rewrite
script, and the full standing-up / refresh procedure — lives in its own repo:
**[snowdesk-tiles](https://github.com/hugorodgerbrown/snowdesk-tiles)**
(SNOW-485). It has no Django code, which is why it is split out of this repo. Go
there for: building the extract, the R2 bucket, DNS, the CORS allowlist, and
refreshing the seasonal snapshot.

The assets are static objects in a Cloudflare R2 bucket, fronted by a Worker
that also serves vector tiles as XYZ (`/tiles/<version>/{z}/{x}/{y}.mvt`) by
range-reading the `.pmtiles` archive. An earlier revision served them from Caddy
on a Render web service with a persistent disk; that is gone.

**The hostname is `tiles.snowdesk-data.info`, on a separate registrable domain
from the site** — so a cookie scoped to `.snowdesk.info` can never ride along on
the hundreds of tile requests a map session fires. It is not a subdomain of
`snowdesk.info`, and it never was in the shipped design.

## What this repo owns — the cutover (SNOW-242)

One change here: **env vars** on the service.

```
OPENFREEMAP_STYLE_URL=https://tiles.snowdesk-data.info/styles/liberty
BASEMAP=openfreemap_liberty
```

That is the whole of it, by design. Two things this runbook used to list are
handled already:

- **CSP needs no edit.** `config/settings/base.py` derives `OPENFREEMAP_ORIGIN`
  from `OPENFREEMAP_STYLE_URL` and puts it in `connect-src`, precisely so the
  two can't drift. Moving origins is an env change, not a deploy.
- **The frontend needs no change.** The rewritten style hands MapLibre a plain
  XYZ `tiles` array, so it is shaped like every other basemap in the catalogue.
  There is no `pmtiles://` source and no `maplibregl.addProtocol` registration —
  that approach was tried and rejected, because a source with no tile URLs is
  something neither the basemap download core's `tileSources`
  (`static/js/basemap_download_core.js`, SNOW-521) nor the service worker's
  offline pinning (SNOW-484) can express.

## The zoom-range trap

The rewritten style **must** declare `minzoom`/`maxzoom` on its vector source.

Upstream Liberty carries the zoom range in the TileJSON its `url` points at, and
the rewrite drops `url` in favour of a `tiles` template. A vector source with no
`maxzoom` defaults to **22** in MapLibre, so the client requests z15+ tiles the
archive does not hold (the Worker answers `204`) instead of overzooming z14. The
symptom is that the basemap looks right until you zoom past 14 and then goes
completely blank — no roads, no labels, no terrain — while every HTTP-level
check still passes.

It reads as *"basemap downloads are broken"*, because a downloaded area pins
z10-14 (`MICRO_BAND`) and relies on overzoom for anything closer in: the
download completes, the roundel goes green, and the area is blank at the zooms
people actually use. The download machinery is not involved.

Fixed in `snowdesk-tiles` (`scripts/rewrite_style.py` +
`TILE_MIN_ZOOM`/`TILE_MAX_ZOOM` in `scripts/config.sh` — both paths in that
repo), and checked by that repo's `scripts/verify.sh` against the Worker's
TileJSON, which reads the range out of the PMTiles header. Nothing in this
repo can detect it.

## The attribution trap (SNOW-640)

**Same line, second casualty.** Dropping `url` also drops the `attribution`
string, which upstream Liberty carries in exactly the same TileJSON as the zoom
range. `scripts/rewrite_style.py` restores `minzoom`/`maxzoom` after the `url`
pop; it does **not** restore `attribution`, so every rewritten source resolves
with `src.attribution === undefined`.

The client consequence: `updateMapAttribution` in `static/js/map.js` unions the
attribution of every source in the active style, so the union is empty and the
legend's **Map data** section has nothing to show. SNOW-640 made that section
collapse rather than paint a heading over a blank line, and log a
`console.warn` so the collapse cannot hide the cause — but collapsing is
presentation, not a fix.

**This is a licence-compliance issue, not cosmetic.** OpenFreeMap serves
OpenStreetMap data under **ODbL, which requires attribution**. A public site
showing none is out of compliance regardless of how tidily the empty panel is
hidden.

The fix belongs in `snowdesk-tiles`, in two parts:

1. `scripts/rewrite_style.py` — set `spec["attribution"]` beside the existing
   `minzoom`/`maxzoom` restoration, read **from the upstream TileJSON** rather
   than hand-written, so the OpenFreeMap / OpenMapTiles / OpenStreetMap credits
   are exactly what the licences require.
2. `scripts/verify.sh` — assert the style declares an attribution, alongside the
   zoom-range check it already makes. Without it this recurs on the next style
   refresh, and as with the zoom range, nothing in this repo can detect it.

## 3-D elevation (`/dem/<version>/`)

SNOW-1444. The 3-D map's elevation tiles are our own copy of
[Mapterhorn](https://mapterhorn.com)'s Terrarium WebP tiles, because
Mapterhorn asks users to copy its archives rather than hotlink
`tiles.mapterhorn.com` ([research](../research/mapterhorn/README.md)). The
copy covers the **supported area** only: every z12 tile that touches a
`MicroRegion.boundary`, plus a ring of one tile round each. With the 461
regions loaded that is about 4,900 z12 tiles, in four separate parts (the
Alps and the Pyrenees among them).

The Worker serves the copy as `/dem/<version>/{z}/{x}/{y}.webp`. It sends
`Cache-Control: public, max-age=31536000, immutable`, which is safe because a
new copy always gets a new `<version>`. A tile outside the copy gets **204**,
like the basemap. `map_3d_core.createDemLoader` treats a 204 as missing and
loads that one tile from AWS instead, so the map is still drawn outside our
area.

**This repo owns** the area and the URL:

```bash
# The area, as GeoJSON. Read-only; writes supported-area.geojson.
uv run python manage.py export_supported_area
```

```
MAP_3D_DEM_TILE_URL=https://tiles.snowdesk-data.info/dem/<version>/{z}/{x}/{y}.webp
```

The CSP's `connect-src` entry comes from that setting, so switching to a new
copy is an env change, not a deploy. **snowdesk-tiles owns** the build
(`scripts/build-dem.sh <version>`, which runs `pmtiles extract --region=` on
the GeoJSON), the Worker route and the publish.

### Seasonal refresh

Once a season, or sooner if Mapterhorn improves a source we use:

1. Run `export_supported_area` against production data, so that any region
   added since the last refresh is covered.
2. In snowdesk-tiles, run `build-dem.sh <new-version>` with that file. Publish
   it, then run its `verify.sh`.
3. Set `MAP_3D_DEM_TILE_URL` to the new version, then open `/map/?view=3d`
   and check the relief.
4. Leave the previous version in R2 until the next refresh.

**Rollback** is step 3 in reverse: set `MAP_3D_DEM_TILE_URL` back to the
previous version, or to Mapterhorn's public URL if our copy is broken. No
deploy is needed.

## Cutover check

Once the origin is verified live (`./scripts/verify.sh` in `snowdesk-tiles`) and
the env vars are set, load `/map/` and confirm:

1. The basemap renders, with no CORS errors in the network panel. A CORS failure
   means the site's origin is missing from `ALLOWED_ORIGINS` in that repo's
   `worker/wrangler.toml` — staging and production are separate entries.
2. **Zoom in past z14 and confirm the map still has roads and labels.** This is
   the check that catches the zoom-range trap above, and the only one that does.
3. Download a region's basemap, then reload offline and confirm the area renders
   at touring zooms.
4. **Open the (i) legend and confirm the "Map data" section is present and names
   the tile providers.** A missing section means the served style carries no
   attribution — the trap above — and `console.warn` will say so. Until part 1
   lands in `snowdesk-tiles`, this check fails by design.
