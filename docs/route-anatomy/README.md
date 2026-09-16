---
name: README
description: Route layers exploded view — Route.points, slope_samples colours, cruxes, no-fall passages, fall_lines, trip bulletin join
status: draft
last-reviewed: 2026-09-16
---

# Route anatomy

[`index.html`](index.html) is a standalone page that takes a saved route apart
one layer at a time, with a real screenshot of the app for each step: the GPX
track, the slope-angle overlay it is measured against, the slope-coloured line
and popup (SNOW-910/960/961), key-passage rings (SNOW-911), no-fall passages
(SNOW-964), fall-line arrows (SNOW-971/974), and the trip page's bulletin
overlap (SNOW-839/962). Each section says what the layer means, how it is
worked out, and what its absence does not mean. The page ends with a table
mapping each layer to its MapLibre layer id and source module.

Open it straight from disk; the images are in [`img/`](img/).

**Status: draft.** Follow-up work is tracked in SNOW-977. The screenshots were
taken with Playwright against a local dev server, on a demonstration route
("Mont Gelé west face") sampled from the live terrain tileset. That route and
the 28 Apr 2026 trip exist only in a local database, so the images can't be
regenerated yet.
