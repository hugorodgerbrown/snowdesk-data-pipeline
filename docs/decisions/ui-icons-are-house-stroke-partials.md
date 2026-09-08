---
name: ui-icons-are-house-stroke-partials
description: UI icons are _icon_*.html partials — 24×24, stroke-2, currentColor, one source per mark; three Font Awesome filled glyphs excepted
status: current
last-reviewed: 2026-09-08
---

# UI icons are house stroke partials

**Decision.** Every icon in the site chrome is a `templates/includes/_icon_*.html`
partial: a `24 24` viewBox, `fill="none"`, `stroke="currentColor"`,
`stroke-width="2"`, `aria-hidden="true"`, with a `size` parameter and an
optional `svg_class`. One partial per mark, and every surface that shows
that mark includes it — an icon is never drawn inline in a calling
template.

Three glyphs are the named exception, and they are filled Font Awesome
Free paths on a 512 grid, each carrying its CC BY 4.0 attribution inline:
the map's favourites star and community-report flag (`static/js/map.js`),
and `_icon_observation.html`'s binoculars. The first two are MapLibre SDF
symbols painted at roughly 18px over aerial imagery, where a 2px stroke
washes out and a filled silhouette does not. The third is the roundel that
opens the observation panel, deliberately heavier than its two neighbours
so a reader can confirm which roundel they tapped — Hugo's choice, recorded
in the partial's own header.

**Why.** The rule exists because of what happened without it. The service-
worker update banner's refresh mark lived inline in
`includes/_overlay_banner.html`, the one glyph in the tree with no partial
behind it, and it was corrupt: a Feather `refresh-cw` that kept the first
arrowhead and one arc, dropped the second subpath, and left the arc's
`L23 10` running as a straight chord back across the circle. It drew, it
was wrong, and it survived a year of review because there was no second
copy to compare it against and no rule saying it should have been a
partial (SNOW-869).

The uniform geometry is what makes copies comparable at all. Two marks at
the same box size read as the same weight only if they share a grid and a
stroke width, so "24×24, stroke 2" is not a style preference — it is the
thing that lets a reviewer see that one glyph is heavier than the rest and
ask why. The three exceptions are legible precisely because they are
enumerated here.

**Consequences.** A new mark means a new `_icon_*.html`, not an inline
`<svg>` in the template that happens to need it first — and a second
surface wanting the same mark includes the partial rather than pasting the
path. Icon partials carry no component-library registry entry (none of the
eighteen do): the library documents composed surfaces, and an icon is a
primitive of one.

A fourth filled glyph needs an argument that answers this file, not a
precedent from the three above — "there is already a filled one" is not
the reason any of them is filled. A path taken from a third-party set
carries its licence attribution in the partial's header comment, as the
existing three do; the project depends on no icon webfont and adding one
would be a separate decision.
