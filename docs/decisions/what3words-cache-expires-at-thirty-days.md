---
name: what3words-cache-expires-at-thirty-days
description: Location.what3words is a 30-day cache read through three_word_address, not a denormalised column — the licence caps it
status: current
last-reviewed: 2026-09-05
---

# A three word address expires; it is not a fact about the place

## Decision

`Location.what3words` and `Location.what3words_fetched_at` (SNOW-840) are a
**cache with an expiry**, not a denormalised value. Nothing outside
`apps/locations/models.py` reads the column: every caller goes through
`Location.three_word_address`, which returns `None` once the stamp is older
than `WHAT3WORDS_MAX_CACHE_AGE` — 30 days.

That constant is **not** a setting, and must not become one. It is a
contractual ceiling, not a tuning knob.

## Why

The what3words API terms permit caching a converted address "solely for
improving the performance of your Product" and "in no event ... more than 30
calendar days". Holding one indefinitely is a licence breach, not a
performance decision.

Everything else in this codebase pushes the other way, which is exactly why
this is written down. The house style is to pre-compute
and denormalise: resolve a value once, out of band, and store it, as
`Location.elevation_m` does. A reader who meets these two
columns will recognise the shape, assume the missing piece is a backfill
command nobody wrote, and "fix" it — by adding one, by dropping the stamp, or
by making the 30 days configurable so an operator can raise it. All three
break the licence, and none of them would look like a breach in review.

Read-time expiry rather than a sweep, because a nightly delete leaves a
window between a row going stale and the sweep running, and a page rendering
inside that window shows something we are no longer permitted to show. The
property closes the window by construction and costs nothing: the row is
already loaded.

No backfill, and no negative caching, for the same reason plus a second one.
The conversions are billed (Basic plan, 1,000/month), so filling the estate
ahead of anyone asking spends money on squares nobody reads *and* starts a
30-day clock on all of them at once. One fill on the first render of a trip
page is both the cheapest pattern and the one that keeps the estate inside
the licence by default.

## Consequences

- A trip nobody has opened for a month re-converts on the next view. That is
  correct, and the coordinate pair covers the moment before it lands.
- Offline, an expired row renders as coordinates rather than as an address.
  Also correct: a stale address is not ours to serve, and the fallback is the
  meeting point stated the way it always was.
- `Location.three_word_address` is the only sanctioned reader. A query that
  filters or annotates on the raw column — a "locations with an address"
  admin filter, a sitemap, an export — bypasses the cap and must not be
  written without dating the row itself.
- Raising the 30 days requires renegotiating the licence, not editing a
  constant.
- If Snowdesk ever needed a *permanent* address for a place, the answer is
  not this column. It would be a different agreement with what3words, and a
  different field with a different name.
