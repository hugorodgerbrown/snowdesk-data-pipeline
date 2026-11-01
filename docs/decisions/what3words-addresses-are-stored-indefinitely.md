---
name: what3words-addresses-are-stored-indefinitely
description: Location.what3words kept forever beside the pin — licence limits are convert-to-coordinates only; Basic plan; w3w's Sept 2026 reply
status: current
last-reviewed: 2026-11-01
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

The address is stored **on the same row as the coordinate it was derived
from**. The pin is the user's own and is the meeting point; the address is
a readable label for it.

Snowdesk calls `convert-to-3wa` and only that. It never calls
`convert-to-coordinates`, which is the endpoint the licence restricts and
meters. `convert-to-3wa` still needs a paid plan — it left the free plan in
November 2024 — and the **Basic plan (£7.99 a month) covers it, unmetered**.

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
in writing in September 2026, correcting an earlier reply of their own (see
**Their confirmation**, below).

**Holding the coordinate beside the address is permitted.** The licence's
restriction is on storing coordinates *obtained from the what3words API* to
avoid a later `convert-to-coordinates` call. Our coordinates are never
obtained from the API: they are the pin someone dropped, held before
what3words is involved. The question Hugo put was whether the rule meant
that, or "no coordinates on the same record as an address, whatever their
source" — which would have forced the pin off the row it defines. what3words
answered the first: "the licence permits your setup directly".

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

### Their confirmation

Hugo wrote to what3words on 7 September 2026 setting out the
coordinate-first flow and asking three questions: whether the 30-day limit
applies to an address derived from our own coordinates; which reading of
the coordinate-storage restriction they meant; and whether Basic includes
`convert-to-3wa`. The reply, from what3words Support & Partnerships,
answered each (quoted as received):

> **Storage duration.** The 30 day limit only applies when you convert a
> what3words address to coordinates. Your flow goes the other way,
> coordinates to address, which falls under a different clause allowing
> storage of up to 100 million address and coordinate pairs indefinitely,
> no 30 day limit, provided it's necessary for your product.
>
> **Storing coordinates alongside the address.** The restriction is about
> coordinates obtained from our API to avoid a future convert to
> coordinates call. Since you never make that call and already hold the
> coordinates as the user's own pin, this doesn't apply to you, the licence
> permits your setup directly.
>
> **Plan and pricing.** Basic at £7.99 a month includes convert to
> what3words address, which is unlimited on every paid plan and isn't part
> of the metered quota. The 1,000 per month limit you saw is for convert to
> coordinates specifically, which you don't need. So Basic covers your use
> case as is.

The reply also withdrew their first answer ("my first reply pointed you at
the wrong pattern"), which had described the opposite flow and said convert
calls needed a Business plan. The original thread is in Hugo's mailbox; this
quote is the project's record of it.

The confirmation holds **only while the flow stays coordinate-first**. It
was given against a stated flow — pin first, one `convert-to-3wa`, a link to
what3words.com rather than a conversion when a reader wants the map — and a
change to any of those is a change to what was confirmed.

## Consequences

- Once converted, an address stays for the life of the pin. The trips list
  and the offline shell keep it rather than falling back to coordinates.
- The pin-move invalidation is now load-bearing. It was a nicety while the
  30 days swept stale rows anyway; it is the only invalidation there is.
- `three_word_address` enforces nothing and is still the only sanctioned
  reader — it normalises `""` and `None` to None, and it is where a policy
  would land if one ever returned.
- The pin stays on the row. No surface needs to drop, round or move the
  coordinate to keep an address beside it.
- **The plan, as of 1 November 2026.** Production runs on the current API
  key while Hugo is the only user. The move to Basic is due when a second
  user arrives — a real person outside the project, not a test account.
  The `what3words` flag is on for everyone there and is kept as a kill
  switch: off falls back to the coordinates and keeps every stored address
  ([`feature-flags.md`](../feature-flags.md)).
- The cost is a flat Basic subscription and nothing per call. Conversions
  being unmetered removes cost as an argument anywhere in this
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
