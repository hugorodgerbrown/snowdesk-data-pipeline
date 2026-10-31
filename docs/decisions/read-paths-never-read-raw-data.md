---
name: read-paths-never-read-raw-data
description: Read paths read Bulletin.render_model and columns, never raw_data; issues_for_date defers it; no per-request rebuild
status: current
last-reviewed: 2026-10-31
---

# Read paths never read raw_data

## Decision

**A read path renders from `Bulletin.render_model` and the row's own
columns — never from `Bulletin.raw_data`** (SNOW-1342). The bulletin page,
the region and place sheets, the favourites card, the problem-terrain
layer and the MCP read tools all hold to it:

- the share card's key message comes from `render_model.prose` and
  `traits[].problems[].comment_html`; article times and the source
  organisation from `Bulletin.issued_at` and `Bulletin.source`;
- `get_current_conditions` / `bulk_current_conditions` take `danger_level`
  from `RegionDayRating.max_rating` (as `get_danger_history` does) and
  `danger_ratings` from `render_model.danger.ratings`;
  `get_avalanche_problems` reads `render_model.traits[].problems[]`;
- `issues_for_date` and `select_bulletins_for_regions` `.defer("raw_data")`.

**No request rebuilds a render model.** `_get_render_model` returns a row's
stored model when it is at `RENDER_MODEL_VERSION` and otherwise the same
`{"version": 0, "error": …}` sentinel a failed build stores, so the page
shows its error card. A `RENDER_MODEL_VERSION` bump ships with a
`Deploy-Step: rebuild_render_models --commit` trailer.

**The raw surfaces are named, and each loads `raw_data` itself:** the
write and apply paths, the rebuild and maintenance commands, the admin,
the `/api/` raw CAAML alternate (`bulletin_caaml`), the MCP
`get_bulletin_raw` tool (by an explicit `values_list("raw_data")` fetch),
and the superuser / `DEBUG` raw embed in `_render_bulletin_page` (the
bulletin page asks `issues_for_date(..., include_raw=True)` for that
audience only, so the embed costs no extra query and the monitored page
count is unchanged). `examples_category`'s filter is exempt until
SNOW-1341.

## Why

`raw_data` is the widest column on the row — 10–30 KB of CAAML each — and
the read paths loaded it on every request while using almost none of it:
a key message, a provider name, a timestamp, each already projected into
the render model or a column. The render model exists so that templates
hold no derivation; a read path that went back to the CAAML for one field
was a second, unversioned parser of the same payload, and two parsers
drift (the MCP problem list and the bulletin page's cards were built from
different sources).

The per-request rebuild was the same cost hidden behind a fallback: it
loaded the CAAML to rebuild a stale model, threw the result away, and did
it again on the next request, so a missed rebuild after a version bump
cost every page view rather than failing visibly once.

## Consequences

- A stale row shows the error card until the rebuild reaches it. The
  Deploy-Step is what keeps that window to a deploy.
- A field a read path needs and the render model lacks is added to the
  builder, with a version bump — not read back out of `raw_data`.
- Removed with the raw readers: `Bulletin.region_count`,
  `get_danger_ratings`, `get_avalanche_problems`, `highest_danger_rating`;
  the `DangerRating` / `AvalancheProblem` dataclasses; the `_extract_*`
  helpers, `build_problem_cards` and `_day_windows_from_raw_ratings`.
- The `raw_data` column itself stays: ingest, rebuilds and the raw
  surfaces above still need it.
