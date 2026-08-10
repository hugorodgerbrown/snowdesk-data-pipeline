---
name: bulletins-yield-to-downloaded-areas
description: The bulletin fill and the downloaded-areas overlay are mutually exclusive — a suppression over the persisted opacity step
status: current
last-reviewed: 2026-08-10
---

# Bulletins yields to the downloaded-areas overlay

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
