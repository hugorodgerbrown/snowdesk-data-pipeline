# Query-count monitoring (SNOW-13)

Per-page SQL query counts are tracked in `perf/query_counts.txt` — a
committed plain-text file with one `<name> <count>` pair per monitored
URL. The Lighthouse CI workflow runs `manage.py monitor_query_counts`
(read-only) after loading fixtures; any mismatch against the baseline
fails the check, so a reviewer sees the delta in the PR diff the same
way they see a Lighthouse-score delta.

## Two surfaces

- `pipeline.middleware.QueryCountMiddleware` attaches an
  `X-DB-Query-Count` header to every response when
  `settings.QUERY_COUNT_HEADER_ENABLED` is truthy — on in
  `development` and `perf`, off in `production`. Useful for ad-hoc
  measurement: open DevTools → Network and read the header.
- `manage.py monitor_query_counts` measures the same counts for a
  fixed URL list via the Django test client and compares / writes the
  `perf/query_counts.txt` baseline.

## Adding a new monitored URL

Append a `(name, url)` tuple to `MONITORED_URLS` in
`pipeline/management/commands/monitor_query_counts.py`, then run
`poetry run python manage.py monitor_query_counts --commit` to seed
the new baseline row.

## When the count legitimately changes

When a new feature touches more of the DB, or adds a new prefetch: run
`--commit` and include the `perf/query_counts.txt` delta in the same
PR so reviewers can sanity-check the new number.
