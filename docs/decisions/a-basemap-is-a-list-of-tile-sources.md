---
name: a-basemap-is-a-list-of-tile-sources
description: Basemap tiles are every vector source x MapLibre's per-tile host rotation — tileSources, tileSourcesKey, register-basemap-origins (SNOW-843)
status: current
last-reviewed: 2026-09-05
---

# A basemap is a list of tile sources, not a tile URL

**Decision.** The download path, the done-probe and the downloaded-tiles
overlay all address a basemap by its **tile sources** —
`string[][]`, one entry per vector source in the live style, each holding
that source's own list of host templates
(`pwaBasemapDownloadCore.tileSources`). The URL for one tile of one
source is chosen by MapLibre's own rule,
`urls[(x + y) % urls.length]`, mirrored in `tileURLForSource`. Equality
between "the basemap this area was downloaded under" and "the basemap on
screen" is a comparison of `tileSourcesKey` values, not of strings.

Separately, the origin allowlist the page posts to the service worker
(`register-basemap-origins`) is seeded from the basemap catalogue and then
**extended from the live style's resolved tile, sprite and glyph URLs**,
accumulating across basemap switches and across sessions.

**Why.** Both halves were single-valued for the project's whole life, and
both were wrong for any real multi-source style. The swisstopo winter
style declares two vector sources (`ch.swisstopo.relief.vt` and
`ch.swisstopo.base.vt`) and lists five hosts
(`vectortiles0-4.geo.admin.ch`) for each. `activeBasemapTileTemplate`
returned the first source's first URL, so a completed region download
pinned **no base tiles at all** and **one fifth of the relief tiles** —
the ones whose indices happen to sum to a multiple of five. Offline, the
map came up blank over a full pinned bucket, and every surface agreed it
was downloaded, because the probe and the overlay asked the same wrong
question the fetcher had answered.

The allowlist had the same shape of error one level up: it is built from
the style DOCUMENT URLs, and swisstopo publishes styles on
`vectortiles.geo.admin.ch` — an origin that serves no tiles. Every
swisstopo tile therefore classified `unclassified` in the worker and was
never opportunistically cached, so the passive half of offline rendering
did not exist for that basemap. The shards were already known elsewhere in
the codebase — `SWISSTOPO_TILE_SHARDS` in `config/settings/base.py` names
all five for the CSP (SNOW-833), for exactly this reason — but that list
is server-side and per-provider; deriving the origins from the live style
instead means the next provider needs no second list.

 It read as an empty allowlist in the
trace but was not one; `origins=5` with a miss is what distinguishes this
from the SNOW-722 hydration failure.

Mirroring MapLibre's selection rather than normalising it away (by
picking one host at download time and rewriting requests in the worker)
keeps one rule in one place: Cache Storage matches on the whole URL, so
the only key that can ever be found is the one the map asks for.

**Consequences.**

- A download costs one tile per cell **per source**, so a two-source
  basemap costs twice the estimate a blob carries. `sourceScaledMb`
  applies that factor at each point the number is spent — the storage
  quota pre-flight, the standing byte budget, the roundel's size readout,
  the custom-area frame (`budgetScaleForBBox`'s `sourceCount`) and the
  over-ceiling backstop. A region that fits under one basemap can be
  refused under another, and says so before the tap.
- The `template` field on a `basemap.regions` / `basemap.customAreas`
  record now carries the whole source spec. The **field name did not
  move**, because the records are persisted per device and one written
  before this ticket holds a bare string there; `tileSources` normalises
  it, so a single-source area downloaded earlier still matches its own
  basemap. A multi-source area recorded earlier correctly stops matching
  — its bucket holds only the fraction of the tiles the bug fetched, and
  re-downloading it is the honest outcome.
- The allowlist accumulates. A stale origin surviving a catalogue change
  is accepted: the allowlist only permits caching, and nothing on the page
  requests a basemap that is no longer offered. The alternative — replacing
  it per style — revokes the previous basemap's origins on every switch,
  and a device that boots offline (where no style document can load, so no
  tile origin is ever learned) would spend the whole session unable to
  classify a single tile.
- Anything that adds a way of addressing tiles — an ESRI-style
  `{z}/{y}/{x}` source, a raster overlay pinned with an area — belongs in
  `basemap_download_core.js`'s tile-source group, not at a call site. The
  fetcher, the probe and the overlay have to keep agreeing, and that is
  only cheap while they share one function.
