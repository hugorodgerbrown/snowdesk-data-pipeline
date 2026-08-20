---
name: resorts-are-search-rows
description: Map search shows a row per resort, not just per region, and ranks by match quality rather than EAWS region id
status: current
last-reviewed: 2026-08-20
---

# Resorts are search rows, and ranking is by match quality

## Decision

The map search index carries **one row per region plus one row per resort**
(`static/js/search_core.js`). A resort row shows the resort's own name, its
parent region on the secondary line, and that region's EAWS id in the badge.
Picking it selects the parent region — the same destination a region row
reaches, differently labelled.

Results are ordered by **match quality**, not by region id: a row whose own
name starts with the query, then one that contains it, then one that matched
on a field the row does not display. Ties break by name, then region id.

## Why

This reverses SNOW-188, which collapsed resorts into their parent region row
and folded resort names into that row's searchable string. Two things
motivated the reversal.

**The data changed.** SNOW-188's stated reason was the resort lists
themselves — "either empty (AT/IT/FR) or long enough that truncating them
adds noise". Since then `Resort.kind` separates lift-served resorts from
touring terrain, `/api/resorts-by-region/` filters to the former, and the
curated sheet has been through several passes. The lists are now short,
populated and trustworthy enough to show.

**The old behaviour was silently lossy.** Folding meant a query for
"Verbier" matched, but the dropdown answered with a region row — the user
never saw the word they typed, and could not tell whether the resort was
known to the app at all.

Match-quality ranking follows from the rows, and is the part most likely to
look arbitrary later. SNOW-188 chose region-id ordering deliberately, to stop
the list reshuffling under a user aiming at it with arrow keys. That holds
with ~75 region rows. It does not survive ~140 resort rows sorted under their
*parent's* id rather than their own name, against a `MAX_RESULTS` of 8: an
exact match on a resort could sit below eight unrelated regions and never
render. A list that cannot show what you typed is worse than one that
reorders. The keyboard concern is answered rather than dismissed — ranking is
a pure function of the query, ties break deterministically, and each extra
character narrows the set, so the best match stays pinned at the top.

## Consequences

- A region query lists that region first and its resorts under it, because
  resort rows carry the parent's name in `searchable` but not in
  `primaryKey`, which lands them in the bottom score band. This is
  deliberate: the box answers "what is in here?" as well as "where is it?".
- A resort whose name equals its region's (Davos in Davos) is **not** emitted
  as a row. It would duplicate a row selecting the same region — the case
  SNOW-188's type badge existed to disambiguate.
- Rows are visually distinguished by a pin glyph, not a text badge; the badge
  slot keeps the region id and country flag SNOW-188 introduced. The
  region/resort *word* exists only for screen readers, via
  `map-strings-template`.
- Resort rows depend on `/api/resorts-by-region/`, fetched at boot. That leg
  degrades to `{}` on failure, so an offline or failed boot shows
  region-only rows — the pre-SNOW-697 behaviour, not an error state.
- `.map-utility-cluster` had to move above `.map-controls-br` in the stacking
  order. Both sat at `z-index: 4`, and the cluster is a stacking context, so
  the dropdown could never rise above the bottom-right roundels — which were
  swallowing clicks on rows 2+. Single-row results had mostly hidden this.
