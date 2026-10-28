---
name: reference-data-has-one-source-of-truth
description: apps/ingest serves all anonymous-map data (resorts, regions, weather, RegionDayRating) to every instance; reference_data()
status: current
last-reviewed: 2026-10-28
---

# Reference data has one source of truth

**Decision.** One service holds all the data needed to power the anonymous
view of the map and its overlays (Hugo, 28 Oct 2026, SNOW-1268). It is
`apps/ingest`, hosted inside production for now. Every Snowdesk instance,
production included, is a client: it syncs that data into read-only copies
of its own tables and keeps user data to itself. Only the service calls the
upstream providers, and the sync is one-way. This widens
[`ingestion-is-a-rehostable-unit.md`](ingestion-is-a-rehostable-unit.md)
from provider data to everything a signed-out visitor sees.

The test for a table: if a signed-out visitor sees it on the map, it comes
from the service; if only a signed-in user does, it stays in the instance.

| Bucket | Tables | Where it lives |
|---|---|---|
| Source of truth | bulletins, weather, reference `Location` rows and resort roles, `Resort`, `Pass` | Written only in the service; synced |
| Code-held | the EAWS region tables (`apps/regions/fixtures/eaws_*.json`), the glossary | Ships with the code; every side loads the same commit |
| Domain derivations | `RegionDayRating`, `RegionBulletin`, `BulletinGrouping`, problem-terrain rules | Computed once in the service; synced |
| Presentation | render models | Rebuilt by each instance |
| User data | accounts, favourites, routes and shares, trips, downloads, field observations, bulletin shares, user-minted `Location` rows | Never leaves its instance |
| Operations | `CommandRun`, `PipelineRun`, request logs, idempotency records, banners, flags | Per instance |

**Why.**

- **One copy of each fact.** Staging, production and cloud sessions showing
  different resorts or different day ratings is a class of bug that a
  synced copy cannot have.
- **Derivations of domain facts travel; presentation does not.** A day
  rating is a pure function of bulletins and regions, so it is computed
  once and every instance shows the same number. A render model follows
  the templates, which change with each instance's deploy.
- **Problem terrain is rules, not pixels.** The service precomputes each
  region's rules for the day. The device still paints them from the
  terrain-class tiles: pre-rendered rasters would need a tile set per
  region per day and would break offline downloads, which hold rules.

**Consequences.**

- `LocationQuerySet.reference_data()` is the line between a synced location
  and a user's own, keyed on the row carrying the service's uuid. `named()`
  cannot be the line, because users can name their favourites. Synced and
  user-minted rows share the `Location` table.
- Each synced row carries the service's uuid
  and the apply upserts by it. User data points at the instance's own
  primary keys, so a favourite on a synced resort survives every sync.
- Deletes travel as `deleted_at` tombstones. A tombstoned row that user data
  still references is hidden, not deleted.
- **Regions stay code-held fixtures, not a feed** (Hugo, 28 Oct 2026). The
  geometry is already one copy of the truth, the repository, versioned with
  the code that reads it, and changes only when EAWS republishes; a feed
  would send 15 MB for no gain. What varied per instance, the region centroid
  `Location` and its elevation, becomes reference locations under SNOW-1253.
- Editing moves to the service: the resort and location sheets and their
  import commands. The staff map-admin overlays stay
  in production, writing the service's tables, until the service has its
  own editor.
- A fresh instance syncs reference data whole and time series as a subset:
  a recent window by default, or the golden week for deterministic testing
  (`pull_reference_data`, SNOW-1272).
- Changing the day-rating or problem-terrain rule needs a service deploy and
  a recompute, not a client deploy.
- Anonymised community reports and the onboarding example route are user
  data, even though signed-out visitors see them. The example route is a
  fixture any instance can load (SNOW-1273).
- Whether this repository becomes the service or the web app moves out is
  undecided.
- The work is staged as SNOW-1270 (derivations),
  SNOW-1253 (weather stations), SNOW-1271 (resorts and passes), SNOW-1272
  (bootstrap and tombstones) and SNOW-1273 (example route).
