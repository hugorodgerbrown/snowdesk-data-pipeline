---
name: one-basemap-per-region-download
description: A region download holds one basemap's tiles at a time; custom areas can hold the same ground twice (SNOW-864, declined)
status: current
last-reviewed: 2026-09-09
---

# A region holds one basemap; a custom area can hold the same ground twice

**Decision.** A downloaded region holds exactly ONE basemap's tiles.
Downloading the same region under a second basemap replaces the first
copy — `map_region_download.js`'s `beforeWarm` asks
(`confirmBasemapReplace`), and `finish` prunes the old basemap's urls
once the new run has succeeded. A CUSTOM area behaves the other way: every
confirmed run mints a fresh `custom-<uuid>` id and bucket
(`generateCustomAreaId`), so the same ground CAN be held twice under two
basemaps that way. SNOW-864 proposed removing the asymmetry by putting the
basemap key into a region's area id; it was declined on 2026-09-08.

**Why the asymmetry is not a defect.** A region has a stable identity that
outlives any one download — `areaIdForRegion` is `'region-' + regionId`,
fixed for the device's lifetime — and the roundel's per-region probe, the
`meta:app` `basemap.regions` record and the account's `DownloadArea` row
all key off it. A custom area has no such identity: it is a rectangle the
user framed once. SNOW-635 gave each confirmed run its own id to fix a
different bug (one `basemap.customArea` row silently replacing the
previous area's tiles), and holding the same ground twice is a side effect
of that, not a capability designed for. The two shapes differ because the
things they identify differ.

**Why not do it anyway.** The basemap would have to enter the identifier,
and three things collapse on that same axis — the Cache Storage bucket,
the `basemap.regions` record (filtered on `region_id`) and
`DownloadArea`'s `UniqueConstraint(user, area_id)`. Fixing one leaves the
other two overwriting each other, so it is all three or nothing. On top of
that, Cache Storage has no rename, so every pre-existing `region-<id>`
bucket needs either a copy-under-a-new-name migration or a reserved
legacy id — the same tail `CUSTOM_AREA_ID` still carries. That is a
substantial change to the most load-bearing identifier in the download
path, and the case it buys (one valley, two national basemaps across a
border; or a second style held as a fallback) has not been asked for.
Holding a region twice also costs roughly twice the bytes against a
standing 500 MB budget.

**Why now rather than left open.** SNOW-871 removed what actually made the
old behaviour hurt. The replacement used to be silent, and destroyed the
existing copy BEFORE the new run had fetched anything, so a failed
download left the user with neither — on the connection this feature
exists for, failing is not the exotic case. It now asks first, only when
the record names another basemap and those tiles are still on disk, and
prunes only after success. What remains is a deliberate limit, not a
sharp edge.

## Consequences

- The roundel's `other-basemap` state keeps its current meaning:
  "downloaded, but for a different basemap — tap to download it for this
  one instead". Not "as well as".
- The Manage downloads sheet shows one row per downloaded region, under
  the basemap it was last downloaded with.
- A cross-border area under two national basemaps is not expressible as
  two region downloads. The workaround is a custom area, which already
  holds the same ground more than once.
- If this is reversed, SNOW-864 holds the worked-through analysis — the
  three collapse points, the migration options, and the interaction with
  SNOW-863's missing-record case, where a heal that reads the record will
  not fire for exactly the devices in the worst state.
