---
name: the-bulletin-terrain-layer-paints-what-it-cannot-decide
description: Problem terrain, four states, never blank; Sync with map terrain, on by default, Steep terrain first; /api/problem-terrain/
status: current
last-reviewed: 2026-11-03
---

# The problem-terrain layer paints what it cannot decide

**Decision.** The layers menu has a "Sync with map terrain" row under
Avalanche bulletins (one line since SNOW-1410: its "From zoom 12,
Switzerland" second line repeated the page's context), and the
region sheet a switch of the same name below the bulletin that writes the
same state (SNOW-1377, 2 Nov 2026; "Highlight problem terrain" from Hugo's
23 Oct 2026 split, and until then the terrain filter's third colour, "By
bulletin", SNOW-1194). It is on by default, for every reader wherever the
terrain tiles are, and gives way to Steep terrain while that is on.
Switched on, every region's ground of 30° and over is painted on the device
from the terrain filter's class tiles, by rules read from that region's
bulletin for the day (`apps/ingest/normalise/problem_terrain.py`, the
service's since SNOW-1270 →
`/api/problem-terrain/` → `static/js/bulletin_terrain_core.js`), in four
states:

| State | When | Painted |
|---|---|---|
| described | a problem's aspects and heights take the pixel in | that problem's EAWS colour; the highest level wins |
| undecided | only a problem the pixel test cannot place takes it in (a treeline band; a prose-only trait) | flat grey, `--color-bulletin-terrain-undecided` |
| undescribed | steep, and no problem describes it | warm-grey `\` hatch, `--color-bulletin-terrain-undescribed` |
| no data | the tiles have nothing | the terrain filter's slate `/` hatch |

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
- **Its own row, not a filter colour (23 Oct).** SNOW-1194 made it a mode
  of the terrain filter, because it is the filter's machinery with its
  settings taken from the bulletins: the same class tiles, the same LRU,
  the same `paintTile`. Hugo found it hidden there (Layers → Basemap →
  Terrain filter → adjust → By bulletin) and not a filter at all: it is the
  bulletin drawn another way, and from z12 it replaces the region colour.
  So it is a row next to the bulletins it comes from (under Avalanche
  bulletins since SNOW-1266),
  with its own state, `overlayState.problem_terrain`
  (`snowdesk.map.overlay.problem_terrain`), and the filter is back to "One
  tint" and "By slope". It still shares the filter's tiles and LRU. (The
  two could both be drawn until SNOW-1377, below.) A device that left the
  filter on in "By bulletin" comes back with this on and the filter off
  (map.js, once, at boot) — kept by SNOW-1377.
- **One state, two switches.** The menu row and the region sheet's switch
  write `window.pwaBulletinTerrain`, and each reads it back from
  `snowdesk:problem-terrain-changed`. Separate stored states would drift.
  SNOW-1297 had put a third at the top of the Steep terrain sheet;
  SNOW-1377 took it out.
- **Behind a flag while the controls were redesigned (SNOW-1374, Hugo,
  2 Nov 2026).** The switches, the legend's key and the `/help/` topic
  rendered only for a reader the `problem_terrain` waffle flag was on for
  (superusers by default). Superseded the same day by the bullet below.
- **On by default, and Steep terrain takes precedence (SNOW-1377, Hugo,
  2026-11-02).** The flag came off: every reader gets the controls
  wherever the terrain tiles are configured (`data-problem-terrain-enabled`
  follows `terrain_filter_eligible`). The layer stays something a reader
  can turn off, but `overlayState.problem_terrain` reads its stored value
  with a default of `true`, so a device that never touched it gets it on
  and one that switched it off keeps it off. Renamed "Sync with map
  terrain", it lives under Avalanche bulletins and in the region sheet,
  and left the Steep terrain sheet: Steep terrain is the reader's own
  tool. While it is on, its colours replace the bulletin's slopes —
  `bulletinTerrainShown()` is `problem_terrain && !terrain_filter`, and
  `setTerrainFilterEnabled` calls `syncBulletinTerrain()`, so the raster
  hides and no region's `terrain` feature state is set (the edge band
  shows) without the stored switch being rewritten. Turning the filter off
  brings the slopes back; switching "Sync with map terrain" on turns the
  filter off. The switches read what is drawn, so they read off while the
  filter is on. This replaces SNOW-1370's "last tap wins".
- **Held to z12 by the layer, not only the source.** The tiles are 256 px,
  so MapLibre asks for z12 tiles at map zoom 11 and the source's `minzoom`
  alone let the layer draw there, over a choropleth that only drops out
  from z12 (Hugo, 23 Oct: both showed at z11). The layer carries
  `minzoom: 12` too.
- **The region sheet's key is folded away.** The strip under the switch is
  a `<details>` that renders closed on every bulletin (Hugo, 23 Oct: a
  reader needs the key once). Its summary asks "What is problem terrain?". The switch's own second
  line is "From zoom 12, Switzerland", always shown (SNOW-1377; it was a
  zoom hint shown only while the map was too far out). Inside are one key
  row per line, the steep hatch named "Additional steep terrain (>30°)",
  and one sentence on what the highlight claims. The map legend's
  "Problem terrain" section is unchanged, because the reader opens it on
  purpose.
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
  region's middle at z12, because the tiles start there.
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
