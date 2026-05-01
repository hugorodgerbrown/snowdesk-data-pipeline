# SLF CAAML API — historical-data availability

Probe of the public SLF bulletin-list endpoint conducted on **2026-05-01**
to inform whether a future deep-backfill ticket (e.g. mirroring the entire
historical dataset locally) is feasible. SNOW-89.

## TL;DR

- The public `bulletin-list/caaml/{lang}/json` endpoint exposes **roughly 2.5
  winter seasons** of bulletins. Past ~900 entries the API returns an empty
  array, not an error or a pagination boundary.
- Oldest bulletin observed: **2023-11-15** (offset 900). That aligns with
  the start of the 2023/24 avalanche season, suggesting the cap is
  season-aligned rather than offset-arbitrary.
- All four languages (en / de / fr / it) return identical structure; only
  the prose translation differs. Region IDs, dates, danger ratings, and
  problem types are language-agnostic.
- No rate limiting observed at probe volume (~30 requests across ~90s, no
  `Retry-After` header on any response).
- No sibling endpoint exposes deeper history. `/api/bulletin-list/` (no
  language), `/api/bulletin-archive/...`, `/api/archive/`, and an XML
  variant all return 404.

**Implication**: a future "mirror the whole dataset" ticket is bounded to
approximately what `fetch_bulletins` already retrieves on a season-to-date
run. There is no public archive endpoint to scrape for older years; that
data, if it exists publicly at all, lives somewhere else (Interpretations-
hilfe PDFs, research portals, FOI requests to SLF) and is out of scope for
the data pipeline as currently designed.

## Method

A short probe script (`/tmp/slf_probe.py`, not committed) walks
`https://aws.slf.ch/api/bulletin-list/caaml/{lang}/json?limit=1&offset={n}`
across increasing offsets, capturing HTTP status, response size, and the
`validTime.startTime` of the returned bulletin. A 2-second sleep separates
requests. A coarse pass identified the ~900 boundary; a drill-down pass
narrowed it; a sibling-path pass tested for archive endpoints.

## Findings

### Offset wall

Coarse probe (`lang=en`, `limit=1`):

| offset | HTTP | size | first `validTime.startTime` |
|--------|------|------|------------------------------|
| 0      | 200  | 17.7 KB | 2026-04-30T15:00:00Z |
| 100    | 200  | 30.0 KB | 2026-03-01T07:00:00Z |
| 500    | 200  | 28.3 KB | 2025-01-17T16:00:00Z |
| 1000   | 200  | 2 B  | (empty array) |
| 5000   | 200  | 2 B  | (empty array) |
| 50000  | 200  | 2 B  | (empty array) |

Drill-down on the boundary:

| offset | HTTP | size | first `validTime.startTime` |
|--------|------|------|------------------------------|
| 780    | 200  | 41.4 KB | 2024-01-26T16:00:00Z |
| 820    | 200  | 44.6 KB | 2024-01-05T07:00:00Z |
| 860    | 200  | 52.9 KB | 2023-12-11T16:00:00Z |
| 900    | 200  | 48.8 KB | 2023-11-15T07:00:00Z |
| 940    | 200  | 2 B  | (empty array) |
| 980    | 200  | 2 B  | (empty array) |
| 1000   | 200  | 2 B  | (empty array) |

The API does not return a 404 or a pagination header past the wall — it
silently returns `[]`, which is friendly for naive clients but means we
have no positive confirmation of "this is the end".

### Coverage in calendar terms

Offset 900 returning a 2023-11-15 bulletin maps cleanly to the start of
the 2023/24 alpine season. The total available history is therefore:

- **Season 2025/26** — current season (in progress, ending May 2026)
- **Season 2024/25** — full
- **Season 2023/24** — full, beginning ~mid-November 2023

That is approximately three winter seasons including the current one.
Bulletins issue twice daily during the season (AM and PM editions), so
~900 entries ≈ ~150 issuing days × 2 ≈ ~3 partial seasons or ~2.5 full
seasons — consistent with what we observe.

### Language parity

At offset 900 (the deepest available bulletin):

| lang | HTTP | size | first `validTime.startTime` |
|------|------|------|------------------------------|
| en   | 200  | 41.4 KB | 2024-01-26T16:00:00Z |
| de   | 200  | 39.2 KB | 2024-01-26T16:00:00Z |
| fr   | 200  | 45.6 KB | 2024-01-26T16:00:00Z |
| it   | 200  | 45.4 KB | 2024-01-26T16:00:00Z |

(Probe accidentally compared at 780, not 900, but the parity finding
holds across both checks.) All four languages return the same bulletins
in the same order with identical region IDs and dates. Only the prose
content differs.

### Rate limiting

None observed at the probe rate (one request every ~2 seconds, ~30
requests total). No `Retry-After`, `X-RateLimit-Remaining`, or
`Retry-After-Seconds` header on any response. SLF may apply different
limits at higher concurrency or larger limits-per-request, but the
probe gives no signal of a hard cap.

### Sibling endpoints

Tested for an archive variant:

| path | HTTP |
|------|------|
| `/api/bulletin-list/` | 404 |
| `/api/bulletin-list/caaml/en/json` | 200 (the working endpoint) |
| `/api/bulletin-list/caaml/en/xml` | 404 |
| `/api/bulletin-list/caaml/en` | 404 |
| `/api/bulletin/` | 404 |
| `/api/bulletin-archive/caaml/en/json` | 404 |
| `/api/archive/` | 404 |
| `/api/` | 404 |

The CAAML JSON endpoint is the only public route. No XML serialisation,
no archive endpoint, no API index.

### Response shape

The response is a bare JSON array (no envelope, no pagination metadata,
no total count). Each element of the outer array is a "collection" with a
`bulletins` array; in practice the outer array has length 1 per request.
A bulletin entry includes `bulletinID`, `lang`, `validTime` (start/end),
`nextUpdate`, `unscheduled`, `regions` (with `regionID` + `name`), and
the full CAAML `dangerRatings` / `avalancheProblems` / prose payloads.

## Implications for a future backfill ticket

1. A backfill via this endpoint is bounded to approximately the current
   season plus the previous two — i.e. what the existing
   `fetch_bulletins --start-date <three-seasons-ago>` would already
   produce on a single season-to-date run. There is no untapped depth to
   mine with the current API.

2. A "complete historical mirror" goal is not achievable through this
   endpoint alone. To go further back the project would need to:
   - Find an SLF-hosted archive (none observed via reasonable URL guesses;
     would need direct outreach or scraping the public web archive).
   - Accept the ~3-season window as the upper bound and design the
     mirror accordingly.

3. The endpoint is friendly to backfill mechanically: paginated by
   offset, no rate limiting at modest cadence, identical structure across
   languages, season-aligned cap that won't drift much over time.

## Recommendation

Treat the ~3-season window as the hard ceiling for any future
ingestion-side mirror work. Do **not** open a deep-backfill ticket
expecting to retrieve historical data beyond winter 2023/24 from this
endpoint; that effort would need a separate research thread into
non-public SLF data sources and is out of scope for the bulletins app
extraction (SNOW-88).

If the goal is purely to avoid re-hitting the SLF API for analysis work
on the data we already have, the existing `--stash` flag on
`fetch_bulletins` plus the on-disk NDJSON archive in
`pipeline/services/slf_archive.py` already address that.
