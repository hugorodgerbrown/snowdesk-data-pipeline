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
`apps/bulletins/services/settled.py`'s `is_settled()` derives the threshold
(`earliest_mutable_date()`) from the **same** `BulletinSource` registry
(`apps.bulletins.services.slf_fetcher.get_sources()`) and the same
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

**Known limitations — the two cases where `is_settled()` can be wrong.**

- **The partial-source case.** `earliest_mutable_date()` drops a `None`
  source entirely from its `min()` — a source with no rows yet imposes no
  constraint on the *others*. But that same source's own
  `Command._default_start_date` returns `settings.SEASON_START_DATE` when
  its `latest_date_fn()` is `None`, so its *next* fetch run resumes from the
  season start, not from wherever the other sources' dates put the
  threshold. Concretely: if ALBINA and Météo-France both have rows but
  SLF's table is empty, `earliest_mutable_date()` is `min(ALBINA, MF)` —
  which is very likely later than `SEASON_START_DATE` — so this module can
  declare a date settled that SLF's next run will still walk straight
  through (it resumes all the way back at the season start). The
  **all-empty** case (every source `None`) is handled correctly —
  `earliest_mutable_date()` also falls back to `SEASON_START_DATE`, matching
  every individual source's own fallback — it's specifically the **mixed**
  case, one source empty while others aren't, where the two disagree.
  Accepted because a genuinely empty provider table outside of a
  cold-started dev/test DB is itself an operational anomaly (a stalled
  pipeline), at which point stale cached geometry is a symptom, not the
  root problem.

- **`latest_slf_date()` is a global, not an SLF-scoped, maximum.** Unlike
  `latest_albina_date()` / `latest_meteofrance_date()`, which filter on
  their own `Bulletin.Source`, SLF's registry entry
  (`apps/bulletins/services/slf_fetcher.py`'s `latest_slf_date()`) calls the
  unfiltered `Bulletin.objects.latest_valid_from_date()` — the most recent
  `valid_from` across **every** provider's rows, not just SLF's. So when
  another provider is further ahead than SLF, `earliest_mutable_date()`'s
  `min()` is really asking "how far has *any* source got", not "how far has
  SLF got". This is safe for **cache correctness** — it can only push the
  threshold *later* (more conservative, fewer dates declared settled), never
  earlier — but it does mean a day SLF genuinely hasn't ingested yet can
  still be declared settled by this module, and SLF's own next scheduled
  run won't necessarily backfill it (it resumes from the same global max,
  not from SLF's actual last date); an explicit re-run/backfill targeting
  SLF is needed to fill that gap. This is pre-existing `fetch_bulletins`
  behaviour, predating SNOW-526 — but SNOW-526 is the first feature whose
  correctness leans on it, so it's named here rather than left implicit.

**Consequences.** The server-side `cache.get_or_set` timeout for the
payload itself stays at 300 s regardless of settled state — lengthening it
would make a manual backfill much harder to flush and buys nothing the
`Cache-Control` header doesn't already deliver for the offline case.

`earliest_mutable_date()` runs a fixed number of DB aggregate queries (one
per registered `BulletinSource`) — cheap in isolation, but
`bulletin_groupings_geojson` needs the answer on every request, including a
payload cache hit. Rather than pay that cost every time, `apps/public/api.py`
memoises the threshold at the call site
(`_cached_earliest_mutable_date()`, `cache.get_or_set` for
`_SETTLED_THRESHOLD_CACHE_TIMEOUT` = 60 s, a key distinct from the
per-`(country, date)` payload cache) rather than inside
`apps.bulletins.services.settled` itself — that module stays a pure, uncached
derivation any other caller can trust for a live answer. A stale memoised
value is safe by construction: staleness can only make the threshold OLDER
(smaller), so it can only under-report which dates are settled, never
wrongly mark a still-mutable one. `tests/public/test_map_api.py`'s
`test_groupings_query_count` warms the memo explicitly (a direct call, not
an HTTP round trip) before measuring, so its query budget reflects steady
warm-cache traffic, not a cold-memo request.

A sparse fixture/dev DB (one provider with no rows) drags the threshold
back to `settings.SEASON_START_DATE`, so nothing looks settled locally
without seeding bulletins for every source or patching `get_sources()` —
tests do the latter rather than relying on seed data.
