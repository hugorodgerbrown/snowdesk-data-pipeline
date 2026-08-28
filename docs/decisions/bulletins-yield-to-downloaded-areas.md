---
name: bulletins-yield-to-downloaded-areas
description: REVERSED by SNOW-663 — bulletin fill vs downloaded-areas overlay exclusivity, a suppression over the opacity step; the hatch ended it
status: historical
last-reviewed: 2026-08-28
---

# Bulletins yields to the downloaded-areas overlay (SNOW-656) — reversed by SNOW-663

**This decision was reversed.** SNOW-663 (2026-08-11, one day after this
document was last reviewed) changed the MARK rather than keeping the two
layers exclusive: the downloaded squares became a 45-degree hatch — a third
of the area in hard strokes, the choropleth's own colour showing through the
rest — which is how an atlas draws one area layer over another. A hatch
makes no colour claim, so the danger colour reads through it and the reason
for the exclusivity went away with the second tint.

The fill and the downloads panel's "Display on the map" switch are now
independent in both directions: `show()`/`hide()` on
`window.pwaDownloadedOverlay` suppress nothing, and raising the fill step
switches no squares off. "Which days are dangerous" and "which areas do I
have offline" are different questions and can be on screen at once.

**What survives, and where it lives now.** The mechanism this document
describes was not undone, only its one-time reason:

- The **preference / effective-visibility split** in
  `static/js/layer_visibility_core.js` stands, with staff resort-edit mode
  (`SUPPRESSION.EDIT_RESORTS`, registered by `map_edit_resorts.js` through
  `window.pwaBulletinsLayer.setSuppressed`) as its only remaining
  suppression. `SUPPRESSION.DOWNLOADS` and the `choose()` transition that
  cleared it were removed when this reversal was documented — nothing had
  set that reason since SNOW-663, and a transition whose whole distinction
  from `setPreference` was clearing it had stopped meaning anything.
- **The off step means invisible, not absent** — the `fill-opacity: 0`
  hit-test consequence below — is live and is documented in
  [`bulletin-fill-is-a-user-choice.md`](bulletin-fill-is-a-user-choice.md)
  and in `regionsFillLayout`'s own docstring.
- **Opening the sheet does not turn the overlay on**, still true and now
  for its own sake rather than the exclusivity's: an implicit repaint of
  the map is a poor trade for discoverability.
- **The `l3` boundary is governed by `bulletins`**, and **`bulletins` stores
  a number that `readBoolStorage` cannot read**. Both unchanged.

One thing below is now doubly out of date: the switch was
`SESSION`-scoped when this was written and is a persisted preference again
(`OVERLAY_STORAGE_KEY.downloads`), and the overlay draws the ACTIVE
basemap's downloads alone. See
[`../offline-map.md`](../offline-map.md)'s "Downloaded-tiles overlay".

---

**Original decision (SNOW-656).**

**Decision.** The bulletin fill (the danger choropleth `regions-fill`, plus
the dissolved `bulletin-groupings-line` boundary) and the downloads sheet's
**"Show areas on the map"** switch can never both be on. The two controls
mirror each other: moving one visibly moves the other. Both may be off at
once — this is mutual exclusion plus restore, not a radio pair.

The fill's own control is a five-step opacity chooser, not a toggle (see
[`bulletin-fill-is-a-user-choice.md`](bulletin-fill-is-a-user-choice.md)), so
"on" here means any step above 0 and the restore has to give back the exact
step the user had.

The state is modelled in `static/js/layer_visibility_core.js` as two things
that are never conflated: a persisted **preference**, and an **effective
visibility** that is the preference AND-ed with any active suppression. The
downloads exclusivity is a suppression (`SUPPRESSION.DOWNLOADS`); staff
resort-edit mode is the other one. `show()` / `hide()` on
`window.pwaDownloadedOverlay` (`static/js/map.js`) are the only writers of the
overlay's visibility, so the lockstep is installed there and every caller —
including any added later — inherits it.

Two consequences of that shape are load-bearing:

- **Only the preference is stored.** There is no remembered second copy to
  restore from, so "turning Show areas off returns the fill to whatever it was
  before" is not a feature that can drift — it is what happens when nothing
  overwrote the preference. A user who had already chosen 0 does not get the
  fill switched back on for them, a user on 0.25 gets 0.25 back rather than a
  default, and a reload while the overlay is on comes back with the real step
  rather than the suppressed 0.
- **The binding is to the overlay's visibility, not to the sheet's
  open/closed lifecycle.** Closing the sheet calls nothing — the in-sheet
  switch is the only caller of `hide()`. Binding the choropleth to "sheet is
  open" would leave the squares and the infill painted together the moment the
  sheet was dismissed, and would repeat the mobile-viewport failure SNOW-645
  already hit when overlay visibility was bound to sheet state.
- **Opening the sheet no longer turns the overlay on.** SNOW-645 called
  `show()` unconditionally from `open()`, for discoverability. That was a
  cheap side effect while the squares were all it changed. With the
  exclusivity in place it stopped being cheap: merely opening the sheet took
  the choropleth off the map and dropped the fill control to 0, neither of
  which the user asked for. An implicit action is a poor trade for
  discoverability when it silently undoes an explicit one.

**Why.** Both overlays paint the same polygons. The download squares are
translucent and are drawn over the choropleth's opaque infill (see
[`choropleth-blended-not-translucent.md`](choropleth-blended-not-translucent.md)),
and the result is unreadable. In practice the two are mutually exclusive
anyway: downloading is a date-independent utility about having the map
available offline, and while you are looking at what you have stored you do not
need that day's danger colours.

Mirroring the controls rather than silently suppressing matters because the
user has to be able to see *why* the colour went away. A choropleth that
vanishes with no control moving reads as a bug.

Splitting the old `l4` overlay key into **Micro regions** (boundary and label)
and the bulletin fill (the data painted onto it) is what makes the exclusivity
tolerable at all: the borders never interfere with anything, so they stay up
throughout while only the infill yields.

**Consequences.** The off step means *invisible*, not *absent*.
`regions-fill` is the map's hit-test target — region selection resolves through
`queryRenderedFeatures(e.point, {layers: ['regions-fill', 'resorts-pin']})` and
the hover cursor hangs off its `mouseenter`/`mouseleave` — and a layer at
`visibility: none` returns nothing from that query. Hiding the fill that way
would leave visible borders that cannot be tapped, with no popup, no pointer
cursor, and nothing in the console to say so. It is dropped to
`fill-opacity: 0` and left installed instead; `visibility` is reserved for the
one case where nothing of the region tier is drawn at all (step 0 AND Micro
regions off), where answering a tap over blank basemap would be worse than not
answering.

Every writer of that layer's visibility goes through
`applyBulletinsVisibility` in `map.js`. `map_edit_resorts.js` used to hide
`regions-fill` directly and now registers a suppression instead
(`window.pwaBulletinsLayer.setSuppressed`) — three independent writers of one
layer's visibility is how this drifts.

The `l3` bulletin-boundary layer is governed by `bulletins` rather than `l4`
(`OVERLAY_VISIBILITY_GOVERNOR`), including for the scrub-time refetch guard: a
boundary hidden because the downloads overlay is on is not refetched.

One trap the step control introduced: `bulletins` stores a NUMBER, so it
cannot be read with `readBoolStorage` — that returns `v === 'true'`, false for
every step including `"1"`. The lazy-load re-seed did exactly that and zeroed
the very preference that had triggered it, a tick after every step the user
picked. Its read goes through `seedFromLegacy` instead.
