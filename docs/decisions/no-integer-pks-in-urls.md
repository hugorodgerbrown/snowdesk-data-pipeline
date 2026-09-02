---
name: no-integer-pks-in-urls
description: No sequential integer PK in a URL or public feed — natural key, stored slug, short opaque id, uuid; Resort slug, Location short_id
status: current
last-reviewed: 2026-09-02
---

# No integer primary keys in URLs

## Decision

**No sequential integer primary key appears in a URL or in a public feed.**
The external identifier is chosen by what the model can guarantee, in this
order — take the first that holds:

1. **A natural key** that is already public, meaningful and stable.
   `MicroRegion.region_id` (`ch-4115`). Unchanged.
2. **A stored unique slug**, where every row is guaranteed a name.
   **`Resort`** — `/resorts/<slug>/`.
3. **A short opaque id**, where a name is not guaranteed.
   **`Location`** — `/weather/<short_id>/`. Eleven URL-safe characters from
   `secrets.token_urlsafe(8)`, the generator `apps/routes/services/shares.py`
   already uses.
4. **`BaseModel.uuid`**, the default when nothing above applies —
   `ResortLocation`, and any future model with no name.

A random capability token is a separate thing and is not on this ladder: for
`/s/<token>/` and `/routes/s/<token>/` the URL *is* the secret. Those are
already correct.

The rule covers **public JSON feeds as well as routes**. A feed that emits a
primary key has exported it just as surely as a URL has.

## Why

The `uuid` field on `BaseModel` exists so that a non-enumerable external
identifier is always to hand at no extra cost, and the codebase already
honours this for favourites and routes. Resorts and locations were the
exception, and the exception leaked further than the routes suggested:
`resorts.geojson` emits `"id": resort.pk` and `weather.geojson` an integer
`location_id`, both to unauthenticated visitors.

**Resort takes a slug rather than a uuid** because it is an SEO landing
surface and the identifier is read by people. `Resort.name_slug` is derived
from `self.name` at read time and carries no unique constraint, so it cannot
be an identifier as it stands; all 186 curated rows slugify distinctly
today, so a real `SlugField(unique=True)` is available.

**Location cannot take a slug**, and the reason is structural rather than
incidental: `link_region_centroid_locations` creates a region centroid with
**no name and no kind** — "it represents the region" — and centroids are
roughly 461 of some 540 public locations. A private pin has no name on the
`Location` at all; its label lives on the `Favourite`. A slug would be
fabricated for the large majority of rows.

A full uuid would work and would match `/favourites/<uuid>/`, but that page
is being removed, and thirty-six characters is a poor canonical URL for a
document meant to be shared. Owner-derived paths (`/weather/ch-4115/`,
`/weather/verbier/base/`) were considered and set aside: the weather page
also serves the owner's own private pin, which has no owner-derived path, so
it would mean two URL shapes for one page.

## Consequences

- **Each entity is one change across three layers** — feed payload, the
  JavaScript that reads it, the route — not a `urls.py` edit.
- **`Resort.slug` and `Location.short_id` are populated by a one-time
  `--commit` management command**, never in the migration that adds the
  column.
- **A resort slug is never regenerated on rename.** Resorts are editable
  data ([`resorts-are-editable-data`](resorts-are-editable-data.md)), and
  regenerating would silently break a live URL and its search ranking.
- **Every changed route keeps a permanent redirect** from its integer form,
  including `/resorts/<id>/<slug>/`, which already 301s to its canonical
  slug and gains one more hop to retire.
- **The staff editor endpoints are in scope**, not exempt. They are the tail
  of the work, but leaving them is leaving the only integer keys in the tree.
- **`MicroRegion` is untouched.** Its `region_id` is the natural key the
  ladder asks for, and the EAWS identifier is the thing readers and search
  engines already have.
