---
name: the-bulletin-terrain-layer-paints-what-it-cannot-decide
description: Problem terrain (SNOW-979, SNOW-1194) — four states, never blank; filter "By bulletin", /api/problem-terrain/, bulletin_terrain_core.js
status: current
last-reviewed: 2026-10-22
---

# The problem-terrain layer paints what it cannot decide

**Decision.** The terrain filter has a third colour, "By bulletin"
(SNOW-1194), and the region sheet a "Show problem terrain on the map"
switch above the bulletin that is a shortcut to it (Hugo, 22 Oct 2026).
Switched on, every region's ground of 30° and over is painted on the device
from the terrain filter's class tiles, by rules read from that region's
bulletin for the day (`apps/bulletins/services/problem_terrain.py` →
`/api/problem-terrain/` → `static/js/bulletin_terrain_core.js`), in four
states:

| State | When | Painted |
|---|---|---|
| described | a problem's aspects and heights take the pixel in | that problem's EAWS colour; the highest level wins |
| undecided | only a problem the pixel test cannot place takes it in (a treeline band; a prose-only trait) | flat grey, `--color-bulletin-terrain-undecided` |
| undescribed | steep, and no problem describes it | warm-grey 135° hatch, `--color-bulletin-terrain-undescribed` |
| no data | the tiles have nothing | the terrain filter's slate 45° hatch |

Ground under 30°, level ground and everything outside a painted region is
left transparent. Morning and afternoon issues are flattened and the worse
drawn — the peak rule of `docs/compressed-views-rating-rule.md`. An empty
aspect list matches every aspect. From z12, where the tiles start, each
painted region's choropleth fill drops out (`feature-state` `terrain`), so
a moderate problem's yellow is not read through a considerable region's
orange; below z12 nothing paints and the choropleth shows as ever.

**Why.**

- A bulletin's aspects and heights mark its core zone, not a boundary.
  SNOW-979's spike over CH-4115 found 72% of the region's steep ground
  outside the day's one problem. Shading only described ground would tell
  a reader that most of the steep ground is fine, so undescribed steep
  ground has a state of its own and blank never means "safe".
- No treeline height exists anywhere in the codebase, and prose-only
  aspects are not structured data; painting either as a match or a miss
  would claim a precision the bulletin does not have
  (`terrain-unknown-is-a-reason-not-a-null.md` is the precedent).
- **Every region at once (SNOW-1194).** SNOW-979 painted the selected
  region alone, from rules its sheet carried as a `json_script`, and the
  layer followed the sheet. Clicking from region to region repainted one
  at a time, and Hugo found it choppy (22 Oct). So the map now fetches one
  payload per day, `/api/problem-terrain/?d=`, with the rules grouped by
  bulletin (one Swiss bulletin covers many micro-regions, so a day is a few
  dozen rule lists, not 149) and each region pointing at its bulletin.
  Each region's issue is picked by `select_default_issue`, the rule its
  page and sheet use, so the map and the text under the switch cannot
  describe different issues.
- **One state, two switches.** "By bulletin" is a mode of the terrain
  filter (`terrain_filter_core.MODES`), because it is the filter with its
  settings taken from the bulletins: the same class tiles, the same LRU,
  the same `paintTile`. The region sheet's switch calls
  `pwaBulletinTerrain.setEnabled`, which sets that mode and switches the
  filter on, or switches the filter off; the sheet reads its state back
  from `snowdesk:terrain-filter-changed`. Two stored states would drift.
  While the mode is set, the filter's own raster is hidden and its sheet's
  slope, aspect and elevation controls step aside for the key.
- Clipping is a pure even-odd scanline fill in Web Mercator
  (`regionMask`), not a canvas `Path2D`, so it is tested in jsdom. A tile
  can straddle several regions with different bulletins, so
  `regionIndex` stacks their masks into one grid of region slots and
  `indexClassifier` sends each pixel to its own region's rules. A tile no
  region touches (box test first, `ringsBBox` / `tileBBox`) is never
  fetched. Masks depend only on geometry, so they are cached across days.

**Consequences.**

- The layer sits ABOVE the choropleth, unlike the terrain filter, which
  sits under it: here the layer is the danger for the regions it paints.
- Switching on from a region's sheet below z12 takes the camera to the
  region's middle at z12, because the tiles start there; zooming out
  afterwards shows the sheet's "Zoom in" line.
- A failed tile gives the regions in it their danger fill back rather than
  leave them blank; a failed feed paints nothing and hides no fill.
- **Offline** the feed is not precached: the browser's HTTP cache holds a
  day already viewed, and nothing else. Adding it to the service worker is
  a follow-up.
- Switzerland only (the tiles are swissALTI3D); outside it, and while
  `TERRAIN_CLASS_TILE_URL` is unset, the sheet draws no switch. SNOW-693
  would widen it.
- It models no runout and is not a hazard map; the help topic
  (`#help-topic-bulletin-terrain`) says so.
- The whole map and a layers-menu control synced to the sheet's switch,
  the follow-up SNOW-979 left, is what SNOW-1194 built: the layers menu's
  terrain filter row is that control.
