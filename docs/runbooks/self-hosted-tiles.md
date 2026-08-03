---
name: self-hosted-tiles
description: Self-hosted basemap origin tiles.snowdesk-data.info — OPENFREEMAP_STYLE_URL cutover, plus the style zoom-range trap blanking it above z14
status: current
last-reviewed: 2026-08-03
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
  something neither `activeBasemapTileTemplate` (SNOW-521) nor the service
  worker's offline pinning (SNOW-484) can express.

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
`TILE_MIN_ZOOM`/`TILE_MAX_ZOOM` in `scripts/config.sh`), and checked by that
repo's `scripts/verify.sh` against the Worker's TileJSON, which reads the range
out of the PMTiles header. Nothing in this repo can detect it.

## Cutover check

Once the origin is verified live (`./scripts/verify.sh` in `snowdesk-tiles`) and
the env vars are set, load `/` and confirm:

1. The basemap renders, with no CORS errors in the network panel. A CORS failure
   means the site's origin is missing from `ALLOWED_ORIGINS` in that repo's
   `worker/wrangler.toml` — staging and production are separate entries.
2. **Zoom in past z14 and confirm the map still has roads and labels.** This is
   the check that catches the zoom-range trap above, and the only one that does.
3. Download a region's basemap, then reload offline and confirm the area renders
   at touring zooms.
