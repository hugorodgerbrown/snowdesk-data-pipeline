---
name: an-address-is-owner-only-on-observations
description: A field observation's what3words address is owner-only — never in community_reports_geojson, where a 3m square undoes coordinate rounding
status: current
last-reviewed: 2026-09-09
---

# A report's address is for the person who filed it

## Decision

A `FieldObservation`'s three word address renders on **the reporter's own
list** (`observation_list`, scoped by `for_user`) and **never** in
`apps.public.api.community_reports_geojson`.

The map's pin popup and the panel row therefore show different things. That
asymmetry is deliberate. `_observation_meta.html` and
`_attach_three_word_addresses` both say so, and
`TestCommunityReportsGeojson` asserts it.

## Why

The two surfaces have opposite audiences. The panel is one user's own
reports; an address there tells them nothing they did not supply
themselves. The overlay is public, unauthenticated, and carries everyone's
reports — and it exists to *anonymise* them. Its docstring commits to it:
coordinates rounded to three decimal places (~80-110 m), never the raw
field, and no account identifier at all.

A three word address names a **3m square**. Publishing one beside an
80-110 m coordinate would not weaken the anonymisation, it would replace
it: the address is roughly thirty times more precise than the thing that
was rounded to protect the reporter. Somebody reports a slab that released
above their line; the overlay is meant to say *roughly where*, and an
address says *exactly where they were standing*.

**This is written down because the change that breaks it looks like a
tidy-up.** The two surfaces were built to match, field for field and in the
same order, so a report reads the same whichever way a user reaches it —
`_observation_meta.html` said so before this decision existed. Adding
`what3words` to the geojson's `properties` dict is a one-line diff that
restores that symmetry, passes review as consistency work, and quietly
publishes precise locations for every report in the last 48 hours.

## Consequences

- `community_reports_geojson`'s `properties` are exactly `type`,
  `type_label`, `observed_at`, `region_name`. A test asserts the set, so
  adding a field fails rather than shipping.
- The panel row and the pin popup no longer match. Anyone restoring that
  symmetry must do it by taking the address OFF the panel, never by adding
  it to the overlay.
- The same reasoning applies to any future public surface carrying other
  people's reports — an export, a feed, an MCP tool. The question is not
  "does this surface show observations" but "whose, and to whom".
- It does not apply to trips or favourites. A trip's meeting point is
  shared deliberately with the people on the trip, and a favourite is the
  user's own pin on their own card; neither passes through an
  anonymisation boundary.
