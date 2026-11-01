---
name: read-paths-never-read-raw-data
description: Read paths use Bulletin.render_model + columns, never CAAML; raw_data dropped (SNOW-1337), raw surfaces read source_caaml or 404
status: current
last-reviewed: 2026-11-01
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
- `issues_for_date` and `select_bulletins_for_regions` deferred
  `raw_data` until SNOW-1337 dropped the column.

**No request rebuilds a render model.** `_get_render_model` returns a row's
stored model when it is at `RENDER_MODEL_VERSION` and otherwise the same
`{"version": 0, "error": …}` sentinel a failed build stores, so the page
shows its error card. A `RENDER_MODEL_VERSION` bump ships with a
`Deploy-Step: rebuild_render_models --commit` trailer.

**The raw surfaces are named, and since SNOW-1337 each reads the ingest
service's CAAML** (`apps.ingest.public.source_caaml`, matched on the shared
`bulletin_id`), because a `Bulletin` keeps none: the admin's detail fields
and CAAML block, the `/api/` raw CAAML alternate (`bulletin_caaml`), the
MCP `get_bulletin_raw` tool, the superuser / `DEBUG` raw embed in
`_render_bulletin_page` (one query, for that audience only), the rebuild
task and the diagnostic commands. Where the service holds no such
bulletin — staging, which pulls its bulletins from production's feed —
the endpoint answers 404, the tool and admin say no CAAML is held, the
embed is left out and the rebuild task leaves the row and fails (Hugo,
1 Nov 2026). `examples_category` was exempt until SNOW-1341, which moved
its filter onto the `Bulletin.danger_levels` column
([why](derived-render-fields-are-stored-once.md)).

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
- The `raw_data` column itself stayed until SNOW-1337, which dropped it:
  the service already held the same CAAML in `SourceBulletin`, and the
  service serves clients the render model, not the payload
  ([reference-data-has-one-source-of-truth](reference-data-has-one-source-of-truth.md)).
