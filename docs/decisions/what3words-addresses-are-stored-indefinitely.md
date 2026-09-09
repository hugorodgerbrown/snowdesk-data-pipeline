---
name: what3words-addresses-are-stored-indefinitely
description: Location.what3words never expires — the licence's 30-day cache cap governs convert-to-coordinates, not an address derived from our own pin
status: current
last-reviewed: 2026-09-09
---

# A three word address is ours to keep

## Decision

`Location.what3words` is stored **indefinitely**. There is no expiry, no
`WHAT3WORDS_MAX_CACHE_AGE`, and `Location.three_word_address` returns the
column whatever its age. `what3words_fetched_at` records when the
conversion happened and is provenance, not a clock.

The only thing that invalidates an address is the pin moving to a different
square, which `edit_location_save` handles by clearing the column at the
point of the move.

## Why

The what3words terms carry two constraints that look like they apply and do
not. One caps a cached address at 30 calendar days; the other meters
conversions at 1,000 a month. **Both govern `convert-to-coordinates`** —
turning an address someone typed into a coordinate pair, where caching the
result would let a product skip a billable call next time.

Snowdesk travels the other way. The organiser's map pin is the input and
the address is derived from it by `convert-to-3wa`, which is unmetered on
every paid plan. Storing what we derived falls under a separate clause
permitting up to 100 million address/coordinate pairs indefinitely where
storage is necessary to the product. what3words confirmed all three points
in writing in September 2026, correcting an earlier reply of their own.

SNOW-840 read the first clause as binding and built accordingly: a 30-day
expiry, a lazy fill on the trip page, no backfill, and a list page that
reads the column but never converts. Each was a sound response to a
metered, expiring resource. None was needed, and the expiry actively cost
something — a trip nobody opened for a month dropped back to a raw
coordinate pair on the list and offline, because the address had silently
gone.

**An address cannot go stale.** It is a deterministic encoding of a fixed
3m square, not an observation about it — `///filled.count.soap` and
`46.080012, 7.318197` say the same thing and will still say it in ten
years. There is nothing for a freshness policy to protect against, which is
why the ceiling was never ours in the first place.

## Consequences

- Once converted, an address stays for the life of the pin. The trips list
  and the offline shell keep it rather than falling back to coordinates.
- The pin-move invalidation is now load-bearing. It was a nicety while the
  30 days swept stale rows anyway; it is the only invalidation there is.
- `three_word_address` enforces nothing and is still the only sanctioned
  reader — it normalises `""` and `None` to None, and it is where a policy
  would land if one ever returned.
- Conversions being unmetered removes cost as an argument anywhere in this
  feature. Where the code still converts once and stores rather than per
  view, the reason is the five-second timeout on a page render.
- Storing an address for a place is now the default rather than the
  exception — see
  [`fill_what3words`](../management-commands.md).
- Supersedes
  [`what3words-cache-expires-at-thirty-days`](what3words-cache-expires-at-thirty-days.md).
  If the direction of travel ever reverses — a surface where somebody types
  an address and we resolve it — the 30-day cap and the 1,000-a-month
  allowance both apply to that path, and neither applies to this one.
