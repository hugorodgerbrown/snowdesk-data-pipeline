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
| Source of truth | bulletins, weather, reference `Location` rows and resort roles, `Resort`, `Pass`, swisstopo's ski-touring network and SAC's tours (`SkiSegment`, `SkiTour`, SNOW-1276) | Written only in the service; synced |
| Code-held | the EAWS region tables (`apps/regions/fixtures/eaws_*.json`), the glossary | Ships with the code; every side loads the same commit |
| Bulletin interpretation | the render model (`Bulletin.render_model`), day character, period transition | Built once in the service; synced with each bulletin |
| Domain derivations | `RegionDayRating`, problem-terrain rules | Computed once in the service from the render model; synced (`SourceDayRating`), or read from the synced model by the service's code (the rules) |
| Region projections | `RegionBulletin`, `BulletinGrouping` | Each instance, from the synced CAAML's region list and the code-held region tables: nothing to interpret, so nothing to sync (SNOW-1270) |
| Presentation | templates, translated labels and copy | Each instance, at render time |
| User data | accounts, favourites, routes and shares, trips, downloads, field observations, bulletin shares, user-minted `Location` rows | Never leaves its instance |
| Operations | `CommandRun`, `PipelineRun`, request logs, idempotency records, banners, flags | Per instance |

**Why.**

- **One copy of each fact.** Staging, production and cloud sessions showing
  different resorts or different day ratings is a class of bug that a
  synced copy cannot have.
- **Interpreting the bulletin belongs to the service; presenting it does
  not** (Hugo, 28 Oct 2026). Despite its name, the render model is not
  presentation. It makes three providers' CAAML look like one, matches
  problems to ratings, validates against the EAWS enums and builds the
  traits. That is a pure function of the provider's data, as is everything
  derived from it: day ratings, groupings, problem-terrain rules. So it is
  built once and every instance reads the same interpretation. The
  instance keeps what really is presentation: the templates and the
  translated labels, looked up from the model's codes at render time.
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
- User points get no weather of their own (Hugo, 28 Oct 2026). A favourite,
  a trip's meeting point or a route's start borrows the nearest curated
  forecast (SNOW-1265), so the weather service never sees a user's
  coordinate and no instance calls Open-Meteo for one.
- Until the service has its own editor, production is the service's
  editor. Its curated `Location`, `Pass` and `Resort` rows are mirrored into
  `SourceLocation`, `SourcePass` and `SourceResort` under their own uuid,
  which becomes their `source_uuid`: `fetch_weather` mirrors the places
  (SNOW-1253), and `mirror_reference_data` all three, hourly (SNOW-1271).
  Passes and resorts are held as documents the service stores and never
  reads, as it treats a bulletin's CAAML.
- Deletes travel as `deleted_at` tombstones. A tombstoned row that user data
  still references is detached, not deleted: it loses its `source_uuid` and
  its estate links (resort roles and pins, region centres), so it leaves
  `reference_data()` while the favourite, trip or field report keeps its
  target (`apps/data_feed/services/reference.py`, SNOW-1272).
- **Regions stay code-held fixtures, not a feed** (Hugo, 28 Oct 2026). The
  geometry is already one copy of the truth, the repository, versioned with
  the code that reads it, and changes only when EAWS republishes; a feed
  would send 15 MB for no gain. What varied per instance, the region centroid
  `Location` and its elevation, becomes reference locations under SNOW-1253.
- Editing moves to the service once it has its own editor: the resort and
  location sheets, their import commands and the staff map-admin overlays.
  Until then they stay in production (Hugo, 28 Oct 2026: keep the admin
  overlays until the new project is working well) and reach the service
  through the mirror.
- A fresh instance syncs reference data whole and time series as a subset:
  a recent window by default, or the golden week for deterministic testing
  (`pull_reference_data`, SNOW-1272).
- Changing how a bulletin is read (a render-model version, the day-rating
  or problem-terrain rule) needs a service deploy and a rebuild, not a
  client deploy. The stored model's shape is the contract between the two,
  versioned by `RENDER_MODEL_VERSION`.
- An instance may extend what it is given (Hugo, 28 Oct 2026). A template
  that needs a field the render model lacks derives it locally from the
  synced bulletin. The field moves into the service only once it has
  proved itself, so a new field never waits on a service deploy.
- Anonymised community reports and the onboarding example route are user
  data, even though signed-out visitors see them. The example route is a
  fixture any instance can load (SNOW-1273).
- Whether this repository becomes the service or the web app moves out is
  undecided.
- The work is staged as SNOW-1270 (the render model and its derivations),
  SNOW-1253 (weather stations), SNOW-1271 (resorts and passes), SNOW-1275
  (the weather backfill), SNOW-1272 (bootstrap and tombstones) and
  SNOW-1273 (example route). Deploying it is
  [`runbooks/reference-data-cut-over.md`](../runbooks/reference-data-cut-over.md).
