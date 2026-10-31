---
name: the-map-surface-stays-light
description: Why map overlays stay light in dark mode — .map-surface-light restates light tokens under .dark; no .dark rules in map.css (SNOW-1334)
status: current
last-reviewed: 2026-10-31
---

# The map surface stays light

## Decision

In dark mode, everything drawn over a map stays light: search and its
dropdown, the region, place and weather sheets, the layers menu, the
roundels, cards, toasts, the date row and the scrubber. Dark mode covers
the site nav and footer and every page off the map (bulletin pages, trips,
settings, help).

A map surface's root carries `.map-surface-light`. Under `.dark`, that
class restates every token `.dark` overrides at its light value
(`src/css/main.css`, section 1c). It also restates `color`, which inherits
as a computed value, and sets `color-scheme: light` for native controls.
Three elements carry it:

- the map page's content wrapper, a `display: contents` div around the
  whole content block, so `#map` and the overlays keep their place in the
  body's flex column;
- the trip page's map;
- the trip meeting picker.

## Why

Hugo compared three treatments on 30 October 2026, rendered from the
running app on an iPad (the mockups are in the project's `dark-mode/`
folder):

- **A. A dark basemap.** Declined. swisstopo publishes no dark winter
  style, so we would have to make and maintain one. A dark ground also
  turns the EAWS danger fills olive, so Moderate stops reading as yellow,
  and it flattens the relief people read slopes from.
- **B. A light map surface.** Chosen. The panels describe the ground under
  them and match it. The danger palette is defined on a light ground, and
  it stays on one.
- **C. Dark panels on a light map (what shipped).** Declined. A dark slab
  over a bright map fights the map for attention. It also kept producing
  contrast bugs: `static/css/map.css` has its own `--ink` / `--muted`
  tokens with no dark value. The search rows sat at 1.2:1 in dark mode,
  and two `.dark` blocks (fifteen selectors) existed only to patch the
  same problem elsewhere.

## Consequences

- The restatement is a copy, because `.dark` has already overwritten the
  light values on `<html>`.
  `tests/public/test_main_css_theme_parity.py` fails if a token `.dark`
  overrides is missing from the block, or if a restated value differs from
  its `@theme` definition.
- `static/css/map.css` holds no `.dark` rules, and
  `tests/public/test_map_page.py` keeps it that way. Map-surface CSS is
  written for light only.
- A new map surface, such as another embedded MapLibre map, takes the class
  on its root. Anything a script appends to `document.body` from a map
  would fall outside the surface and needs the class itself. Nothing does
  that today.
- Scripts that read a token for something drawn on the map read it off
  `.map-surface-light` rather than `<html>` (`buildFallbackStyle`,
  `basemapIdentityColour`).
- If the app ever ships a dark basemap, reopen this. Option A's problem
  with the danger fills would come back with it.
