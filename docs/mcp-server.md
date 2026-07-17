---
name: mcp-server
description: MCP JSON-RPC 2.0 server at POST /api/mcp/ — search_regions, get_current_conditions, get_danger_history, list_resorts_in_region tools
status: current
last-reviewed: 2026-07-17
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
* Advertises protocol version `"2025-11-05"` in `initialize`; a
  client-requested version is accepted but not required to match — the
  server always states its own preferred version and lets the client
  decide whether to continue, per spec.

## Methods

| Method | Notes |
|--------|-------|
| `initialize` | Returns `protocolVersion`, `capabilities: {"tools": {}}`, `serverInfo: {name: "snowdesk", version: <APP_VERSION>}`. |
| `notifications/initialized` | A notification (no `id`) — accepted, no response body (`204`). |
| `ping` | Liveness check; empty result `{}`. |
| `tools/list` | Returns all four tools below with `name`, `description`, `inputSchema`. |
| `tools/call` | `{"name": ..., "arguments": {...}}` → a `CallToolResult` (`content`, `structuredContent`, `isError`). |

## Tools

All four are implemented in `mcp_server/tools.py`, composed from services
that already exist elsewhere in the codebase — no new query logic.

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
fingerprinted on `max(updated_at)` across the three source tables — any
edit is a guaranteed cache miss, no explicit invalidation call site
needed.

### `get_current_conditions`

Returns the danger rating and forecaster prose for one region on one day.

* **Params:** `region_id` (string, required), `date` (`YYYY-MM-DD`,
  optional — defaults to today).
* **Returns:** `{region_id, region_name, date, has_bulletin, danger_level,
  danger_ratings, prose: {snowpack_structure, weather_forecast,
  avalanche_activity}, summary}`. When no bulletin covers `date`,
  `has_bulletin` is `false` and the rest is a structured "no data" result
  — a quiet day with no forecast is a legitimate outcome, not an error.

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

## Cost caps

* `get_danger_history` is capped to one region and one avalanche season
  per call (above).
* `search_regions` returns at most 10 candidates.
* Every tool takes a single, already-resolved `region_id` except
  `search_regions` — the LLM is expected to call `search_regions` first
  when it only has a place name, then pass the returned `region_id` to the
  other three tools.

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
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.0.1"}}}'

curl -s -X POST http://localhost:8000/api/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

curl -s -X POST http://localhost:8000/api/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_regions","arguments":{"query":"Verbier"}}}'
```

## Known v1 limitations

* **No language-pair aliasing.** Fuzzy matching handles accents, typos,
  punctuation, and whitespace, but not name pairs that share no letters —
  `Sion` (fr) vs `Sitten` (de), `Coire` (fr) vs `Chur` (de). If usage shows
  this matters, a curated `RegionAlias` table is the natural follow-up
  rather than trying to guess these fuzzily.
* **Major-region representative.** A `search_regions` hit on a
  `MajorRegion` name (e.g. "Wallis"/"Valais") resolves to one
  alphabetically-first child `MicroRegion`'s `region_id` — a best-effort
  representative for major regions with multiple children, since a major
  region has no bulletin page of its own.
* **Auth.** Public/anonymous, IP-rate-limited — matches the rest of
  `/api/`. No token auth in v1.

## See also

* [`docs/telemetry-pipeline.md`](telemetry-pipeline.md) — the other
  CSRF-exempt, rate-limited, stateless JSON POST endpoint in this
  codebase; `mcp_endpoint` mirrors its shape.
* [`docs/compressed-views-rating-rule.md`](compressed-views-rating-rule.md)
  — the peak (`max_rating`) semantics `get_danger_history` reads.
* [`docs/map-and-api.md`](map-and-api.md) — the GET JSON/GeoJSON API this
  server composes on top of.
