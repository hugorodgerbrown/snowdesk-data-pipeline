---
name: mcp-server
description: MCP JSON-RPC 2.0 server at POST /api/mcp/ — thirteen read-only tools over regions, bulletins, problems, danger trend, geolocation
status: current
last-reviewed: 2026-07-18
---

# MCP server

Snowdesk hosts a small, read-only [MCP](https://modelcontextprotocol.io)
server so an LLM client (Claude Desktop, claude.ai's remote-MCP UI, or any
other MCP client) can query current conditions and danger-rating history
directly, rather than scraping rendered bulletin pages. `/llms.txt`
advertises the endpoint under its own "MCP server" section.

## Endpoint and transport

```
POST /api/mcp/
Content-Type: application/json
```

The endpoint speaks plain **JSON-RPC 2.0 over a single Django POST view**
(`mcp_server/views.py::mcp_endpoint`) — there is no `mcp` SDK, no ASGI, no
Starlette. Snowdesk is WSGI-only (`gunicorn config.wsgi`), and the MCP
Streamable-HTTP spec permits a POST-only endpoint that returns
`application/json` and rejects GET with 405; that is exactly what a plain
Django view provides, so implementing the JSON-RPC envelope directly
sidesteps standing up an ASGI stack for this one surface.

* **Trailing slash is optional.** Both `POST /api/mcp/` (canonical) and
  `POST /api/mcp` route to the same view. The slash-less alias exists
  because remote MCP connectors POST `initialize` to exactly the URL the
  user typed, and `APPEND_SLASH` cannot redirect a POST to the canonical
  URL without dropping the body (the client replays the 301 as a GET → the
  405 below), so a URL pasted without the slash would otherwise fail to
  connect. See `mcp_server/urls.py`.
* **`GET /api/mcp/`** → `405` with `Allow: POST`.
* **CSRF-exempt** — JSON-RPC clients cannot mint CSRF tokens, and every
  tool is read-only, so the CSRF risk surface is empty. Same rationale as
  `analytics.views.telemetry_receive` (`docs/telemetry-pipeline.md`).
* **Rate-limited** to 60 requests/minute per source IP
  (`django-ratelimit`, `block=True`) — an over-limit request is rejected
  automatically with a bare `403`, before the view body runs.
* **`Cache-Control: no-store`** on every response — every reply is
  per-request JSON-RPC state, never safe to cache.
* **Stateless** — no `Mcp-Session-Id` handling; each request is
  independent. No batched JSON-RPC requests in v1 (one request object per
  POST body).
* Negotiates protocol version in `initialize`: if the client's requested
  `protocolVersion` is one this server recognises (`2024-11-05`,
  `2025-03-26`, `2025-06-18`, or `2025-11-25` — see
  `mcp_server/protocol.py::SUPPORTED_PROTOCOL_VERSIONS`), the server
  echoes it back; otherwise it returns its preferred version
  (`"2025-06-18"`, `PROTOCOL_VERSION`) and lets the client decide whether
  to disconnect, per spec.

## Methods

| Method | Notes |
|--------|-------|
| `initialize` | Returns `protocolVersion`, `capabilities: {"tools": {}}`, `serverInfo: {name: "snowdesk", version: <APP_VERSION>}`. |
| `notifications/initialized` | A notification (no `id`) — accepted, no response body (`204`). |
| `ping` | Liveness check; empty result `{}`. |
| `tools/list` | Returns all thirteen tools below with `name`, `description`, `inputSchema`. |
| `tools/call` | `{"name": ..., "arguments": {...}}` → a `CallToolResult` (`content`, `structuredContent`, `isError`). |

## Tools

All thirteen are implemented in `mcp_server/tools.py`, composed from
services that already exist elsewhere in the codebase — no new query
logic.

**Casing convention (SNOW-404):** `list_regions` and `region_info`
deliberately emit **UPPER CASE** response constants (`kind: "MICRO"`,
provider values `"SLF"` / `"ALBINA"` / `"METEOFRANCE"` — the
`Bulletin.Source` enum *names*, never the raw lowercase literals) with
case-insensitive input normalisation. Every other tool below uses the
lowercase constants shown in its own `Returns` line. This is an approved
inconsistency, not an oversight — see each tool's section for its exact
casing.

### `search_regions`

Fuzzy-searches `Resort` / `MicroRegion` / `MajorRegion` names by a
free-text place name. Tolerates missing/wrong accents, typos, punctuation,
and whitespace variance (`Val d'Isere`, `Zermat`, `St Anton`, `Val-Thorens`
all resolve). Call this first when you only have a place name rather than
an exact `region_id`.

* **Params:** `query` (string, required).
* **Returns:** `{query, results: [{region_id, name, kind, parent, score}], count, summary}`.
  `kind` is `"micro"` (a bulletin region), `"major"` (an EAWS L1 region —
  resolves to one representative child micro-region), or `"resort"`.
  Ties are broken micro > major > resort. Empty `results` on no match — a
  successful response, not an error.

Implementation: `mcp_server/resolvers.py::search_places` — NFKD ASCII-fold
+ lowercase + `sankt`/`st.` → `saint` + punctuation strip
(`mcp_server/normalise.py::normalise`), scored with
`rapidfuzz.process.extract(scorer=WRatio, score_cutoff=70)`. The candidate
pool (~1500 rows) is cached in the Django default cache under a key
fingerprinted on `max(updated_at)` **and the row count** across the four
source tables (`MicroRegion`, `MajorRegion`, `Resort`, `RegionAlias`) —
any create, edit, or delete is a guaranteed cache miss, no explicit
invalidation call site needed. The count is what covers deletes
(SNOW-552): removing a row whose `updated_at` is not the maximum leaves
the timestamp aggregate unchanged, so a timestamp-only fingerprint went
on serving the deleted row for up to the one-hour `_POOL_TTL`.

### `get_current_conditions`

Returns the danger rating and forecaster prose for one region on one day.

* **Params:** `region_id` (string, required), `date` (`YYYY-MM-DD`,
  optional — defaults to today).
* **Returns:** `{region_id, region_name, date, has_bulletin, danger_level,
  danger_ratings, prose: {snowpack_structure, weather_forecast,
  avalanche_activity}, summary}`. When no bulletin covers `date`,
  `has_bulletin` is `false` and the rest is a structured "no data" result
  — a quiet day with no forecast is a legitimate outcome, not an error.

### `get_avalanche_problems`

Returns the structured avalanche-problem list for one region on one day —
the decision-support detail behind the scalar `danger_level` returned by
`get_current_conditions`. Sourced from `Bulletin.get_avalanche_problems()`
(`bulletins/models.py`), which parses the bulletin's `avalancheProblems`
CAAML array into `AvalancheProblem` dataclasses (`bulletins/schema.py`).

* **Params:** `region_id` (string, required), `date` (`YYYY-MM-DD`,
  optional — defaults to today).
* **Returns:** `{region_id, region_name, date, has_bulletin, problems: [{
  problem_type, danger_rating_value, valid_time_period, elevation:
  {lower_bound, upper_bound} | null, aspects, comment, avalanche_size}],
  count, summary}`. `elevation` is `null` when the source problem carries
  no elevation constraint; otherwise both bounds are always present,
  either of which may itself be `null`. When no bulletin covers `date`,
  `has_bulletin` is `false` and `problems`/`count` are empty/`0` — the
  same structured "no data" shape as `get_current_conditions`. A bulletin
  with an empty `avalancheProblems` array is a distinct, non-error case
  (`has_bulletin: true`, `problems: []`, and a different summary wording)
  from "no bulletin at all".

**Curated field subset.** Deliberately omits provider-specific fields —
`subdivision` (SLF's CH sub-level qualifier), `avalanche_type` (ALBINA's
slab/loose/glide axis), `snowpack_stability`, and `frequency` — as
low-value for a general-purpose LLM caller. See
`AvalancheProblem`'s docstring in `bulletins/schema.py` for the full field
set these are drawn from.

### `get_danger_history`

Returns per-day minimum/maximum danger ratings for one region over a date
range.

* **Params:** `region_id` (required), `from_date` / `to_date`
  (`YYYY-MM-DD`, required), `min_rating` (optional:
  `low|moderate|considerable|high|very_high`).
* **Returns:** `{region_id, region_name, requested_from, requested_to,
  effective_from, effective_to, season_start, season_end, clamped, days:
  [{date, min_rating, max_rating}], count, count_at_or_above_min_rating,
  summary}`.

**Cost cap:** the requested range is always clamped to a single avalanche
season (1 November → 31 May) — the season containing today, or, off
season (June–October), the most recently completed one
(`mcp_server/season.py::current_or_last_season`). `clamped` reports
whether either bound was adjusted. A range entirely outside the season
window returns `days: []` with `clamped: true` and the season bounds — a
successful empty response, not an error. `days` is always the **full,
unfiltered** list for the effective range; `min_rating` narrows only the
`count_at_or_above_min_rating` metric (computed against each day's peak
`max_rating`, per the compressed-views peak rule —
`docs/compressed-views-rating-rule.md`) — it does not remove rows from
`days`.

### `list_resorts_in_region`

Lists the geocoded ski resorts within one micro-region — the same
`Resort.objects.geocoded()` filter as `/api/resorts-by-region/`, scoped to
a single region.

* **Params:** `region_id` (string, required).
* **Returns:** `{region_id, region_name, resorts: [{name, latitude,
  longitude, canton}], count, summary}`.

### `get_bulletin_metadata`

Returns provenance metadata for the bulletin covering one region on one
day — the fields a downstream LLM needs to quote freshness or attribute
the source without hallucinating.

* **Params:** `region_id` (string, required), `date` (`YYYY-MM-DD`,
  optional — defaults to today).
* **Returns:** `{region_id, region_name, date, has_bulletin, bulletin_id,
  issued_at, valid_from, valid_to, next_update_expected, source_provider
  ("slf"|"albina"|"meteofrance"|null), source_url, language,
  language_variants_available, summary}`. `source_url` prefers the
  bulletin's stored `pdf_url` and falls back to the provider's landing
  page when none is recorded. `language_variants_available` reports the
  languages Snowdesk has stored for this bulletin — currently always a
  single-item list (`Bulletin.bulletin_id` is unique so only one language
  variant survives per row). When no bulletin covers `date`,
  `has_bulletin` is `false` and provenance fields are omitted — a
  structured "no data" result, not an error.

### `get_bulletin_raw`

Escape hatch: returns the full CAAML v6 payload (wrapped in the GeoJSON
Feature envelope Snowdesk stores) for one region on one day. Use this
when the flattened tools have dropped a field the caller needs — payload
is ~10-30 KB per bulletin, so it is not the default.

* **Params:** `region_id` (string, required), `date` (`YYYY-MM-DD`,
  optional — defaults to today).
* **Returns:** `{region_id, region_name, date, has_bulletin, provider,
  issued_at, caaml: {type: "Feature", geometry, properties: {...}},
  summary}`. When no bulletin covers `date`, `has_bulletin` is `false`
  and the `caaml` key is omitted.

### `bulk_current_conditions`

Batched fan-out over `get_current_conditions`. Prevents a "show me
Valais today" prompt from turning into 20+ sequential MCP round-trips
through a client that is already going through an LLM.

* **Params:** `region_ids` (array of strings, 1..20, required), `date`
  (`YYYY-MM-DD`, optional — defaults to today).
* **Returns:** `{query: {region_ids, date}, results: [...], count,
  summary}`. Each entry in `results` matches
  `get_current_conditions`'s response shape when the region resolves,
  or `{region_id, has_bulletin: false, error: "unknown_region",
  summary}` when it doesn't.
* **Cost cap:** hard-capped at 20 region_ids per call (see
  `_BULK_CURRENT_CONDITIONS_CAP`). An empty or over-cap batch is
  rejected as JSON-RPC `-32602` (invalid params), not silently
  truncated. Unknown region_ids inside a valid batch are per-item
  errors — one stale id doesn't wipe out the other 19.
* **Duplicates** in `region_ids` are allowed and return duplicate
  entries in `results` — the caller's own bug, not something for the
  tool to hide.

### `find_regions_near`

Reverse geolocation: given `(lat, lon, radius_km)` return the covered
avalanche-warning regions inside the radius, nearest first. Skiers know
their trailhead coordinates from a mapping app but not the SLF region
slug — `search_regions` covers the opposite direction (place name →
region).

* **Params:** `lat` (number, required, `[-90, 90]`), `lon` (number,
  required, `[-180, 180]`), `radius_km` (number, required, positive,
  capped at 100), `limit` (integer, optional — default 10).
* **Returns:** `{query: {lat, lon, radius_km}, results: [{region_id,
  name, kind: "micro", distance_km, provider}], count, summary}`.
  Nearest first. `provider` is derived from the region's country
  (`"slf"` / `"albina"` / `"meteofrance"`).
* **Cost cap:** `radius_km` is hard-capped at 100 km per call (see
  `_FIND_REGIONS_NEAR_RADIUS_CAP_KM`). Over-cap radius, out-of-range
  lat/lon, non-positive radius, and non-integer `limit` are all
  rejected as JSON-RPC `-32602` (invalid params).
* **Distance:** pure-Python haversine
  (`mcp_server.resolvers._haversine_km`) against
  `MicroRegion.centre` — no PostGIS or Shapely dependency, matching
  the request-path point-in-polygon precedent.
* **Empty results** when nothing is inside the radius (e.g. a query
  point outside Snowdesk's coverage) — a legitimate empty response,
  not an error.

### `get_danger_trend`

Returns the danger rating series for one region over the trailing N
days plus a computed trend layer — answers "is danger rising or
falling in Verbier?" in one call.

* **Params:** `region_id` (string, required), `days` (integer,
  optional — default 14, cap 90).
* **Returns:** the full `get_danger_history` envelope (season-clamped)
  plus three fields:
  - `direction`: `"rising"` / `"stable"` / `"falling"` from a mean-
    shift comparison of the first vs last third of the window; the
    threshold is `_TREND_DIRECTION_THRESHOLD` (0.5 rating steps).
  - `change_point`: `{date, from_rating, to_rating}` for the most
    recent transition, or `null` if the window is flat.
  - `current_streak`: `{rating, days}` for the trailing consecutive
    days at the current peak rating, or `null` if the window is empty.
* **Cost cap:** window capped at 90 days (see `_TREND_MAX_DAYS`).
  Non-positive or over-cap `days` values raise a domain-level
  `isError` — this is the same convention as
  `get_danger_history`'s `min_rating` rejection (a tool that tried
  and couldn't satisfy), not a JSON-RPC parameter failure.
* **Off-season windows** clamp to the season window and return `days:
  []` with `direction: "stable"`, `change_point: null`,
  `current_streak: null` — a structured empty result, not an error.

### `list_regions`

Lists every avalanche-warning micro-region Snowdesk knows about,
optionally filtered by country and/or provider. Runs with no arguments to
return the full reference list.

* **Params:** `country` (string, optional — `CH`/`AT`/`IT`/`FR`, matched
  case-insensitively), `provider` (string, optional —
  `SLF`/`ALBINA`/`METEOFRANCE`, matched case-insensitively).
* **Returns:** `{filters: {country, provider}, regions: [{region_id,
  name, kind: "MICRO", country, provider}], count, summary}`, ordered by
  `region_id` (the model default).
* **Filters are orthogonal and ANDed** — supplying both narrows to their
  intersection. An unrecognised `country` or `provider` value raises a
  domain-level `isError`, not a JSON-RPC parameter error — same
  convention as `get_danger_history`'s `min_rating` rejection.
* **Uncovered regions are included.** A `MicroRegion` with no
  `RegionDayRating` rows is still a real, listable region; `region_info`
  is where the coverage window (or its absence) is reported.
* **UPPER CASE response constants** (`kind: "MICRO"`, `provider:
  "SLF"`/`"ALBINA"`/`"METEOFRANCE"`) — see the casing note above.
* **No result cap** — the full list runs ~150-300 rows, per the approved
  SNOW-404 scope.

### `region_info`

Returns full reference-data metadata for one micro-region: its EAWS
parent hierarchy, source provider, resorts, a computed bounding box, its
`RegionDayRating` coverage window, and a static published issue schedule.

* **Params:** `region_id` (string, required).
* **Returns:** `{region_id, region_name, country, eaws_parent:
  {major_prefix, major_name, sub_prefix, sub_name}, source_provider,
  resorts: [{name, latitude, longitude, canton}], resort_count, bbox:
  {min_lon, min_lat, max_lon, max_lat} | null, coverage_first_date,
  coverage_last_date, issue_schedule, summary}`.
* **`bbox`** is computed in pure Python from `MicroRegion.boundary`
  GeoJSON (`Polygon` or `MultiPolygon`) — no Shapely, no PostGIS, the
  same dependency-free precedent as
  `mcp_server.resolvers._haversine_km`. `null` when the boundary is
  missing or malformed.
* **`coverage_first_date`/`coverage_last_date`** come from
  `RegionDayRating.objects.filter(region=region).aggregate(Min("date"),
  Max("date"))`; both `null` for a region with no ingested day ratings —
  a legitimate outcome, not an error.
* **`issue_schedule`** is a static, hand-written string describing each
  provider's published cadence (SLF ~17:00 CET daily, +08:00 update in
  season; ALBINA ~17:00 for the following day, +08:00 when needed;
  Météo-France ~16:00 local per massif in season) — general publication
  information, not a live per-bulletin field.
* **Elevation bounds were assessed and dropped (SNOW-404).** Analysed
  against a season of bulletins and found not to add reliable value at
  the region level (bounds vary per avalanche problem, not per region) —
  no `elevation_min`/`elevation_max` field is returned. Revisit only if a
  concrete caller need emerges.
* **UPPER CASE response constants** (`source_provider:
  "SLF"`/`"ALBINA"`/`"METEOFRANCE"`) — see the casing note above.
* Raises a domain-level `isError` for an unknown `region_id`.

### `get_regional_snapshot`

Returns a per-region peak-danger entry for every region in a country or
major-region scope, plus a scope-level summary — answers "where should
I go?" in one call instead of an N-region fan-out over
`get_current_conditions` (149 calls to cover Switzerland alone). A
natural companion to `bulk_current_conditions` for the case where the
caller doesn't yet know which `region_ids` they want.

* **Params:** exactly one of `country` (ISO-3166-1 alpha-2, e.g. `"CH"`)
  or `major_region_id` (a `MajorRegion.prefix`, e.g. `"CH-4"`) is
  required; both are mutually exclusive. `date` (`YYYY-MM-DD`, optional
  — defaults to today).
* **Returns:** `{scope: {country, major_region_id}, date, regions: [{
  region_id, name, danger_level, min_rating, has_bulletin}], count,
  count_with_bulletin, summary}`. `regions` is ordered by `region_id`
  (`MicroRegion.Meta.ordering`, alphabetical) and includes every region
  in scope regardless of `display_on_map` — this is a coverage query,
  not a map-visibility one. `danger_level` (the day's peak rating) and
  `min_rating` are `null` when `has_bulletin` is `false` — a region with
  no bulletin on that day is a legitimate outcome, not an error.

**Source: `RegionDayRating`, not a per-region bulletin scan.** A naive
implementation would call the equivalent of `get_current_conditions`
once per region — for CH's 149 MicroRegions, 149 sequential lookups per
`tools/call`. Instead this tool issues one indexed
`RegionDayRating.objects.filter(region__in=…, date=…)` query.
`danger_level` reads `RegionDayRating.max_rating` — the same
peak-rating semantic already established for the choropleth, map
tooltip, and season calendar (`docs/compressed-views-rating-rule.md`)
and already read by `get_danger_history` / `get_danger_trend`.
`min_rating` is included alongside it at zero extra query cost (same
row) — split-day variability is exactly what a "where to go" caller
wants visibility into.

* **Cost:** no per-call cap. The largest scope (`AT`, 153 regions) costs
  one indexed row lookup — roughly the same as a single
  `get_danger_history` call, not a 153x fan-out.
* **Validation:** supplying both `country` and `major_region_id`,
  supplying neither, an unrecognised `country`, or an unrecognised
  `major_region_id` all raise a domain-level `isError` — the same
  convention as `get_current_conditions`'s unknown `region_id`
  rejection.

## Cost caps

* `get_danger_history` is capped to one region and one avalanche season
  per call (above).
* `search_regions` returns at most 10 candidates.
* `bulk_current_conditions` caps its `region_ids[]` input at 20 entries per
  call.
* `find_regions_near` caps its search radius at 100 km per call.
* Most bulletin-scoped tools (`get_current_conditions`,
  `get_avalanche_problems`, `get_bulletin_metadata`, `get_bulletin_raw`,
  `list_resorts_in_region`) take a single, already-resolved `region_id`.
  Start with `search_regions` or `find_regions_near` when you only have a
  place name or coordinates, then pass the returned `region_id` to the
  bulletin-scoped tools. `get_regional_snapshot` and
  `bulk_current_conditions` are the exceptions — they cover a scope or a
  batch in one call.

## Error codes

Standard JSON-RPC 2.0 reserved codes (`mcp_server/protocol.py`):

| Code | Meaning | When |
|------|---------|------|
| `-32700` | Parse error | Request body isn't valid JSON. |
| `-32600` | Invalid Request | Not a JSON object, wrong/missing `jsonrpc`, missing `method`. |
| `-32601` | Method not found | Unrecognised top-level JSON-RPC method (e.g. `resources/list`). |
| `-32602` | Invalid params | `params`/`arguments` not an object, or `tools/call` names an unregistered tool. |
| `-32603` | Internal error | An unhandled exception in a method handler — logged server-side, generic message to the client. |

A **domain-level** tool failure (unknown `region_id`, malformed date, bad
`min_rating`) is *not* a JSON-RPC error — `tools/call` still returns a
normal `result` envelope with `isError: true` and a human-readable
`content` message, per the MCP convention that a tool's own failure is a
successfully-routed call the tool couldn't satisfy.

## Pointing a client at it

**MCP Inspector** (local smoke test):

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP: http://localhost:8000/api/mcp/
```

**Claude.ai / Claude Desktop remote MCP:** paste the deployed
`https://<host>/api/mcp/` URL into the client's "add a remote MCP server"
UI — no manifest or `.well-known` discovery is implemented in v1 (the
`/llms.txt` link is the discovery path).

**curl**, three-request smoke test:

```bash
curl -s -X POST http://localhost:8000/api/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0.0.1"}}}'

curl -s -X POST http://localhost:8000/api/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

curl -s -X POST http://localhost:8000/api/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_regions","arguments":{"query":"Verbier"}}}'
```

## Known v1 limitations

* **Language-pair aliasing is curated, not exhaustive.** Fuzzy matching
  handles accents, typos, punctuation, and whitespace, but not name pairs
  that share almost no letters (`Sion` (fr) vs `Sitten` (de), `Loèche`
  (fr) vs `Leuk` (de)). `regions.RegionAlias` (SNOW-409) closes this gap
  with a hand-curated `(region, alias_text)` table — `search_places` folds
  its rows into the candidate pool alongside `MicroRegion`/`MajorRegion`/
  `Resort` names (`mcp_server/resolvers.py::_build_candidate_pool`). Only
  ~10 rows are seeded so far (see `regions/fixtures/region_aliases.json`)
  — each was verified against the committed EAWS fixtures before being
  added, so coverage is intentionally narrow rather than guessed. A query
  with no curated alias and no letter overlap with the canonical name
  (e.g. `Coire` for `Chur`) still returns no match; add a row to the
  fixture as usage surfaces more of these.
* **Major-region representative.** A `search_regions` hit on a
  `MajorRegion` name (e.g. "Wallis"/"Valais") resolves to one
  alphabetically-first child `MicroRegion`'s `region_id` — a best-effort
  representative for major regions with multiple children, since a major
  region has no bulletin page of its own.
* **Auth.** Public/anonymous, IP-rate-limited — matches the rest of
  `/api/`. No token auth in v1.
* **No `resources/*` capability.** Reference data (`list_regions`,
  `region_info`) is exposed as `tools/call` methods, not as MCP
  `resources/list` / `resources/read` — `initialize` advertises only
  `capabilities: {"tools": {}}`. A `resources/*` request is unrecognised
  (`-32601`), same as any other unimplemented top-level method.

## See also

* [`docs/telemetry-pipeline.md`](telemetry-pipeline.md) — the other
  CSRF-exempt, rate-limited, stateless JSON POST endpoint in this
  codebase; `mcp_endpoint` mirrors its shape.
* [`docs/compressed-views-rating-rule.md`](compressed-views-rating-rule.md)
  — the peak (`max_rating`) semantics `get_danger_history` reads.
* [`docs/map-and-api.md`](map-and-api.md) — the GET JSON/GeoJSON API this
  server composes on top of.
