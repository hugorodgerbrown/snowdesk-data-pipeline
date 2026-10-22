---
name: the-bulletin-terrain-layer-paints-what-it-cannot-decide
description: Problem terrain layer (SNOW-979) — four states, never blank; terrain_rules, bulletin_terrain_core.js, pwaBulletinTerrain, selected region
status: current
last-reviewed: 2026-10-22
---

# The problem-terrain layer paints what it cannot decide

**Decision.** The region sheet carries a "Show problem terrain on the map"
switch above the bulletin (Hugo, 22 Oct 2026). Switched on, the selected
region's ground of 30° and over is painted on the device from the terrain
filter's class tiles, by rules read from the day's bulletin
(`apps/bulletins/services/problem_terrain.py` → `static/js/bulletin_terrain_core.js`),
in four states:

| State | When | Painted |
|---|---|---|
| described | a problem's aspects and heights take the pixel in | that problem's EAWS colour; the highest level wins |
| undecided | only a problem the pixel test cannot place takes it in (a treeline band; a prose-only trait) | flat grey, `--color-bulletin-terrain-undecided` |
| undescribed | steep, and no problem describes it | warm-grey 135° hatch, `--color-bulletin-terrain-undescribed` |
| no data | the tiles have nothing | the terrain filter's slate 45° hatch |

Ground under 30°, level ground and everything outside the region is left
transparent. Morning and afternoon issues are flattened and the worse
drawn — the peak rule of `docs/compressed-views-rating-rule.md`. An empty
aspect list matches every aspect. While the layer paints a region, that
region's choropleth fill drops out (`feature-state` `terrain`), so a
moderate problem's yellow is not read through a considerable region's
orange.

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
- **The sheet brings the rules.** The body already holds the bulletin it
  renders (`_resolve_selected_issue`), so it carries the rules as a
  `json_script` and the map cannot paint a different issue from the text
  under the switch. No endpoint, no service-worker rule, nothing per day
  on the server; offline, the layer has exactly what the sheet has.
- **The selected region only, for now.** The switch is in that region's
  sheet, so the layer follows the sheet: `showRegion` when a body paints,
  `clearRegion` when the sheet closes — a layer left behind by a closed
  sheet would have no switch to turn it off. The reader's on/off is
  remembered (`snowdesk.map.bulletin_terrain`).
- Clipping is a pure even-odd scanline fill in Web Mercator
  (`regionMask`), not a canvas `Path2D`, so it is tested in jsdom. A tile
  the region misses is never fetched.

**Consequences.**

- The layer sits ABOVE the choropleth, unlike the terrain filter, which
  sits under it: here the layer is the danger for the region it paints.
- Switching on below z12 takes the camera to the region's middle at z12,
  because the tiles start there; zooming out afterwards shows the sheet's
  "Zoom in" line.
- Switzerland only (the tiles are swissALTI3D); outside it, and while
  `TERRAIN_CLASS_TILE_URL` is unset, the sheet draws no switch. SNOW-693
  would widen it.
- It models no runout and is not a hazard map; the help topic
  (`#help-topic-bulletin-terrain`) says so.
- **Follow-up, not built:** the whole map, with a layers-menu row synced to
  the sheet's switch (Hugo, 22 Oct). That is when an all-regions rules
  endpoint becomes worth having.
