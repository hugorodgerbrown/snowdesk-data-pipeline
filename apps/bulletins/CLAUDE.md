# Bulletin providers

Three providers, one canonical storage shape (CAAML v6 JSON); all fetched
via the `fetch_bulletins` command.

- **SLF** (`apps/bulletins/services/slf_fetcher.py`) — paginated CAAML list API,
  no auth, no date filter:
  `https://aws.slf.ch/api/bulletin-list/caaml/{lang}/json?limit={n}&offset={n}`.
  Reverse-chronological; the pipeline pages until it passes the start-date
  boundary. Historical depth limits: [`docs/slf-api-history.md`](docs/slf-api-history.md).
- **ALBINA** (`apps/bulletins/services/albina_fetcher.py`) — EUREGIO
  avalanche.report CDN, no auth; per-day CAAML v6 JSON URLs for the AT-07,
  IT-32-BZ, and IT-32-TN regions. 404 means "no bulletin".
- **Météo-France** (`apps/bulletins/services/meteofrance_fetcher.py`,
  `meteofrance_translator.py`) — DPBRA XML per massif behind an API key
  (`METEOFRANCE_API_KEY`), translated to the CAAML v6 shape that
  `upsert_bulletin` expects
  (mapping spec: [`docs/meteofrance-mapping.md`](docs/meteofrance-mapping.md)).

Raw bulletins from all three providers are wrapped in a GeoJSON Feature
envelope before storage — `{ type: "Feature", geometry: null, properties: {…} }`
([why](docs/decisions/geojson-feature-envelope.md)).

**Canonical payload examples** live in `tests/sentinels/` — three graded
cases (A single-level, B structurally-enhanced, C split-day multi-problem)
per provider, each with a README, enforced by a round-trip test. Read a
sentinel before reasoning about any provider's payload shape; don't trust
prose descriptions of the schema.

A full 2025/26 season for all three providers is committed under
`apps/bulletins/local_mirrors/*.ndjson` (~30 MB, git-tracked) — written by
`fetch_bulletins --stash` and replayed by the dev-mirror views. For testing
behaviour **across** days, load the *golden week* from it with
`seed_test_week --commit`: seven consecutive real days, all three providers,
morning and evening issues, a real danger swing
([`docs/decisions/golden-week-derived-not-committed.md`](docs/decisions/golden-week-derived-not-committed.md)).
Don't replay sentinels onto a shared date to simulate a day — that manufactures
region overlaps that cannot occur upstream.

Relative links above are written from the repo root.
