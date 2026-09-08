---
name: the-base-layer-band-follows-the-basemap-extent
description: The base layer's zoom band is per basemap — BASE_LAYER_BANDS, baseLayerBand, z0-9 for national styles, z0-7 for OpenFreeMap
status: current
last-reviewed: 2026-09-08
---

# The base layer's band follows the basemap's extent

**Decision.** The shared base layer's zoom band is a function of the
**basemap**, not a global constant. `BASE_LAYER_BANDS`
(`static/js/basemap_download_core.js`) gives `swisstopo_winter`,
`swisstopo_light`, `ign_plan` and `basemap_at` **z0–9** — abutting
`MICRO_BAND`'s z10 floor — and `openfreemap_liberty` **z0–7**, which is
also `BASE_LAYER_BAND`, the default an unmeasured basemap gets.
`baseLayerBand(key)` is the one lookup; `baseLayerBlob` and
`baseLayerTileURLs` take the key, and omitting it keeps the default.

**Why.** The band's cost is set by how much ground the style covers, and
the four basemaps do not cover comparable ground. Measured by fetching
tiles, z8+z9 over each basemap's own extent is:

| basemap | z8 | z9 | total |
|---|---|---|---|
| openfreemap_liberty (global, so camera-clamped, Alps-wide) | 33.4 MB | 88.0 MB | **121 MB** |
| swisstopo (CH, both sources) | 1.4 MB | 3.0 MB | **4.4 MB** |
| ign_plan (FR) | 2.1 MB | 2.3 MB | **4.4 MB** |
| basemap_at (AT) | ~3.2 MB | ~4.1 MB | **~7.3 MB** |

121 MB is a quarter of the standing 500 MB budget spent before a single
area is downloaded. 4.4 MB is a rounding error, and it buys away the seam
an offline reader hits between a download's z10 floor and the base layer
— for every Swiss, French and Austrian reader.

This is the third ruling on the band, and it is not a flip-flop.
SNOW-856 shipped z0–9 so the two bands would meet, and never measured.
SNOW-863 measured the **default** basemap, found 144.4 MB, and trimmed to
z0–7 everywhere. SNOW-868 measured the other three. SNOW-863's ruling was
right for OpenFreeMap and was never a statement about a national style;
the OpenFreeMap figure above independently reproduces the 123 MB that
ticket recorded, which is what makes the other three trustworthy.

**Consequences.**

- No migration, and none is needed. `resolveBaseLayerPlan` threads the
  basemap key into `baseLayerTileURLs`, so `_baseLayerBucketIsStale`
  compares against *that basemap's* url set by construction rather than
  by a second lookup that could drift. The national bands **widen**, so
  an existing national bucket is a strict **subset** of the new band —
  not stale, nothing evicted, nothing re-fetched, and the ordinary
  missing-url plan tops it up with z8 and z9. Only OpenFreeMap's
  pre-SNOW-863 z0–9 buckets are supersets, and dropping those is what
  that path already existed to do.
- The gap must not be closed for the **default** basemap on tidiness
  grounds. MapLibre draws the nearest cached ancestor
  (`findLoadedParent`), so z8 and z9 render from the stored z7 tile —
  softer, never blank, the same trade SNOW-856 already accepted for
  ground outside a download.
- A basemap added to `BASEMAP_STYLES` and not to `BASE_LAYER_BANDS` gets
  z0–7. That is the cheap direction, and the right default for an extent
  nobody has measured.
