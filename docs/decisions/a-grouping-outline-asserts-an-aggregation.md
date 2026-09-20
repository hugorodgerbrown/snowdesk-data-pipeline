---
name: a-grouping-outline-asserts-an-aggregation
description: A BulletinGrouping row exists only where a bulletin covers two or more boundaried micro-regions; one region gets no outline
status: current
last-reviewed: 2026-09-20
---

# A grouping outline asserts an aggregation

**Decision.** A `BulletinGrouping` row is written only when a bulletin links
**at least two** micro-regions carrying a `boundary`. The threshold is
`MIN_GROUPED_REGIONS` in `apps/bulletins/models.py`, read by the ingest-time
writer (`compute_bulletin_grouping_boundary`), by
`BulletinGroupingQuerySet.degenerate()`, and by the candidate queryset in
`backfill_bulletin_groupings`. A bulletin below the threshold gets no row, and
any row it already has is deleted — on re-ingest by the writer, and in bulk by
`purge_degenerate_bulletin_groupings` (SNOW-1001).

**Why.** The layer exists to say *these regions are covered by one text*. With
one region there is nothing to say: `unary_union` over a single polygon returns
that polygon, so the row caches a duplicate of the region's own
`MicroRegion.boundary` and `/api/bulletin-groupings.geojson` serves an outline
that lands exactly on `regions-line`. The user sees a line they were already
seeing, drawn to assert a grouping the warning service never made.

The codebase had already reached this conclusion from the other end:
`seed_test_data` partitions regions into *contiguous groups of more than one*
precisely because a one-region outline "looks identical to no grouping at all
and defeats the point of the layer" (SNOW-534). The guard makes the writer
agree with the seeder.

It also matters at rest. Measured across `apps/bulletins/local_mirrors/*.ndjson`,
Météo-France is 1:1 — 4,671 of 4,671 bulletins cover one massif — so its entire
grouping estate is duplicate geometry. ALBINA stays 99% multi-region and is the
layer's real subject. SLF is aggregated today and will become 1:1 when SNOW-998
lands, at which point its rows join the same category.

**Consequences.**

- **An absent outline is information, not a gap.** A day that draws none means
  every provider on screen issued one bulletin per region. Neither the endpoint
  nor the map should treat an empty FeatureCollection as an error.
- **The threshold counts boundaried regions, not links.** A bulletin covering
  three regions of which one has geometry dissolves to that one polygon, so it
  is degenerate too. Every query that selects on the count must carry the same
  `boundary__isnull=False` filter; the constant is shared but the filter shape
  is not, which is what the partition test in
  `tests/bulletins/test_bulletin_grouping_model.py` pins.
- **Deletion is a command, not a migration.** A bulk delete inside a migration
  locks the table for the length of a Render deploy, so the existing rows are
  removed by `purge_degenerate_bulletin_groupings --commit` as a post-deploy
  step ([catalogue entry](../management-commands.md)).
- **`backfill_bulletin_groupings` cannot select on `grouping__isnull=True`
  alone.** A bulletin below the threshold matches that filter for ever, so a
  backfill using it re-attempts the whole Météo-France archive on every run and
  reports the result as a backlog of skips. Its candidate queryset carries the
  boundaried-region count for that reason.
- **This is not a fidelity reduction.** Nothing about the bulletin's content is
  hidden; the row was a cache of geometry the micro-region layer already
  carries. The provider's text, ratings and problems are unaffected.
