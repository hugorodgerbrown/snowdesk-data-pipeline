---
name: date-aware-cache-policy
description: bulletin-groupings.geojson Cache-Control uses a fetcher-derived settled threshold; the SW persists only what the response marks immutable
status: current
last-reviewed: 2026-07-26
---

# Date-aware cache policy for bulletin-groupings.geojson

**Decision.** `/api/bulletin-groupings.geojson?d=<date>` (SNOW-526) sets a
`Cache-Control` header that depends on whether `<date>` is *settled* — no
future ingest run can still change that day's dissolved boundary geometry.
`bulletins/services/settled.py`'s `is_settled()` derives the threshold
(`earliest_mutable_date()`) from the **same** `BulletinSource` registry
(`bulletins.services.slf_fetcher.get_sources()`) and the same
`latest_date_fn()` calls that `fetch_bulletins.Command._default_start_date`
already uses to pick its default `--start-date`. There is no second,
hand-maintained constant for "how far back is safe" — the boundary is
whatever the fetcher would actually resume from.

A settled date gets `public, max-age=604800, immutable`; today and the
still-mutable tail keep the pre-existing `public, max-age=300`.
`static/js/sw.js`'s `_staleWhileRevalidate` only writes
`/api/bulletin-groupings.geojson` to the shell cache when the response it
just fetched carries the `immutable` token — `shouldPersist()` in
`static/js/basemap_cache_core.js` — rather than re-deriving "is this date
settled" client-side.

**Why derive the threshold from the fetcher registry instead of a
constant.** A standalone constant (e.g. "settled = more than N days old")
would drift from reality in both directions: SLF publishes daily and rarely
needs more than a one-day overlap, while a provider outage or a historical
backfill can leave "the latest bulletin" much further in the past than N
days, silently marking recently-fetched dates as settled before they
actually are. Reusing `get_sources()` / `latest_date_fn()` means the cache
boundary can only be wrong if the fetcher's own resume logic is wrong — and
a wrong resume logic is a bug regardless of this feature. The anti-drift
test in `tests/bulletins/services/test_settled.py`
(`test_earliest_mutable_date_agrees_with_fetch_bulletins_default_start_date`)
pins the two together so a future change to the fetcher's default-start
logic fails there rather than shipping a silently stale cache boundary.

**Why the worker reads the response's own directive rather than
re-implementing the date rule.** The alternative — teaching `sw.js` "a date
is settled if it's before X" — would duplicate server-side logic in the
worker and require keeping the two in sync on every deploy (the worker has
no reliable way to fetch `earliest_mutable_date()` itself without an extra
round trip). Reading `Cache-Control: immutable` off the actual response the
worker already has in hand is both simpler and can't drift: whatever the
server decided is exactly what gets persisted, by construction.

**Why 7 days, not the year-long `max-age` historic bulletin pages use.**
Settled bulletin-groupings geometry is immutable on the *normal* ingest
path, but `backfill_bulletin_groupings --commit` and `fetch_bulletins
--force` can still rewrite history deliberately. A year-long `max-age`
would leave a stale HTTP/CDN-cached copy for far too long after such a
manual rewrite. Offline availability doesn't depend on `max-age` at all —
Cache Storage entries persist until the SW's own version bump or LRU trim
evicts them — so shortening this window costs nothing on the offline path
and only bounds the shared-cache staleness window.

**Consequences.** The server-side `cache.get_or_set` timeout for the
payload itself stays at 300 s regardless of settled state — lengthening it
would make a manual backfill much harder to flush and buys nothing the
`Cache-Control` header doesn't already deliver for the offline case.
`is_settled()` runs a fixed number of DB aggregate queries (one per
registered `BulletinSource`) on every request to this endpoint, including
cache hits — accepted as a small, bounded cost, tracked in
`tests/public/test_map_api.py::test_groupings_query_count`'s query budget.
A sparse fixture/dev DB (one provider with no rows) drags the threshold
back to `settings.SEASON_START_DATE`, so nothing looks settled locally
without seeding bulletins for every source or patching `get_sources()` —
tests do the latter rather than relying on seed data.
