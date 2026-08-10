---
name: bulletin-fill-is-a-user-choice
description: The danger choropleth is translucent at one of five user-chosen opacity steps, 0 being off — replacing the opaque pre-blended fill
status: current
last-reviewed: 2026-08-10
---

# How strongly the choropleth paints is the user's call

**Decision.** `regions-fill` is painted at one of five opacity steps — **0,
0.25, 0.5, 0.75, 1** — chosen by the user and persisted per device under
`snowdesk.map.overlay.bulletins`. The default is **0.5**. The colours are the
raw `RATING_COLOURS`, not composited against anything.

**0 is the off position.** It is not a separate control: the step control
replaced an on/off toggle, and off is simply the weakest end of the range.
This is why the stored preference is a number rather than a boolean, and why
`layer_visibility_core.js` talks about an *effective opacity* rather than a
visibility.

The control is a roundel in the bottom-right stack (`#map-fill-toggle`),
first in `#map-controls-collapsible` so it sits under locate and hides with
the strip, opening a flyout to the left. Five discrete steps rather than a
range input: each is then a one-tap 44px target, and what gets persisted is
one of five known values rather than an arbitrary float nobody chose.

**Why.** How strong the choropleth should be is not a binary. It depends on
the basemap underneath, on whether the reader is after the danger rating or
the terrain it sits on, and on the display. A toggle forced that judgement
into all-or-nothing and the answer was usually neither: an opaque fill buries
the terrain, and switching it off loses the reason the map exists.

This reverses
[`choropleth-blended-not-translucent.md`](choropleth-blended-not-translucent.md),
which made the fill opaque so a rating rendered identically on every basemap.
That problem is real and returns with this change — the same rating does read
differently against swisstopo's hillshade than against openfreemap's warm
land. The trade is deliberate: the reader can now see what is under the
colours, and can dial the fill up to 1 to get the old constancy back.

**Consequences.**

- **Compositing is off.** `map.js` no longer calls `compositeOverBackdrop()`.
  Compositing and translucency do the same job and applying both blends every
  rating twice — the no-bulletin grey composited to within four points of the
  backdrop, so at 50% opacity it vanished entirely. Only one may be in force.
- **The off step means invisible, not absent.** `regions-fill` is the map's
  hit-test target (`queryRenderedFeatures`, and the hover cursor's
  `mouseenter`/`mouseleave`), and a layer at `visibility: none` returns
  nothing from that query. At step 0 the layer stays installed at
  `fill-opacity: 0`, so borders remain tappable. `visibility` is reserved for
  the one case where nothing of the region tier is drawn at all — step 0 AND
  Micro regions off — where answering a tap over blank basemap would be
  worse than not answering. The whole table is in
  `regionsFillLayout`'s docstring.
- **Selection emphasis rides the step.** It used to be a heavier blend
  weight; it is now `step × (0.85 / 0.55)`, capped at 1, so a selected region
  reads stronger than its neighbours at every step rather than at a fixed
  value that could fall below the resting one.
- **The step is not a visibility.** It is a *preference*, AND-ed with any
  active suppression — see
  [`bulletins-yield-to-downloaded-areas.md`](bulletins-yield-to-downloaded-areas.md).

**Unresolved.** No single step suits all five basemaps: the darker ones wash
the colours out at 0.5 in a way openfreemap does not. Per-basemap defaults
are the obvious next move if this proves annoying in use; the control makes
it cheap to find out first.
