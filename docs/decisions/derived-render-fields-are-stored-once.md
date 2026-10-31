---
name: derived-render-fields-are-stored-once
description: render_model["derived"] built once by the ingest builder — terrain_rules, state, danger_levels; derived_of, state_of; Bulletin.danger_levels
status: current
last-reviewed: 2026-10-31
---

# Derived render fields are stored once

## Decision

**What a reader computes from a render model, the builder computes once
and stores in it** (SNOW-1341). `build_render_model` ends by writing
`render_model["derived"]`: the problem-terrain rules, the period
transition, the problem family, the five inputs of the headline matrix,
the alert state with its fingerprint, and the distinct danger levels.
The readers — `/api/problem-terrain/`, the region sheet's key, the region
body's transition and headline, the alerts run and the "since" strip —
read it through `derived_of` / `state_of` / `period_transition_of`
(`apps.ingest.public`).

- `bulletin_state` and `BulletinState` moved into ingest
  (`apps/ingest/normalise/bulletin_state.py`) so the builder can call
  them; `apps/bulletins/services/bulletin_change.py` re-exports them. The
  fingerprint is byte-identical — the send log is keyed on it — and
  `tests/ingest/normalise/test_bulletin_state.py` pins it per sentinel.
- The headline's **copy** is not stored: it is localised. Only its inputs
  are.
- `danger_levels` is copied to `Bulletin.danger_levels`
  (`"|moderate|considerable|"`), so `/examples/<level>/` filters a column
  instead of casting `raw_data` to text. The column is not indexed: a
  `LIKE '%|high|%'` cannot use one.
- The bulletins feed item carries the service's index of the CAAML —
  `valid_from`, `target_date`, `region_codes` — and `upsert_bulletin` uses
  them as sent, reading the CAAML only when an item lacks them. Additive,
  so the feed version did not move.
- The problem-terrain feed and the region sheet take the day's issue from
  `RegionDayRating.source_bulletin` (`select_related`), the issue that
  rated the day, instead of re-running the selection rule over the day's
  `RegionBulletin` links.

## Why

The render model exists so templates hold no derivation, but every page
view still derived the same six things from it: terrain rules for 149
regions on every problem-terrain fetch, the alert state twice per alerted
place per run, the transition and headline inputs on every body. Each was
a pure function of the stored model, so computing it per read bought
nothing but cost; storing it puts it under `RENDER_MODEL_VERSION`, so a
change to any of them is a rebuild that reaches every row, not a silent
divergence between rows read before and after a deploy. It also makes the
service's reading the one every instance uses: a client copying a current
model takes its derived values with it.

## Consequences

- A change to any derived value is a builder change: bump
  `RENDER_MODEL_VERSION` and ship `Deploy-Step:
  rebuild_source_render_models --commit` then `rebuild_render_models
  --commit`.
- `derived_of` computes the block for a model that has none: the
  version-0 sentinel a failed or stale row reads as, and a pre-v10 row the
  rebuild has not reached (the alerts run reads `Bulletin.render_model`
  without a version check). The result is what the readers computed
  before, so output does not depend on which path a row took.
- A region whose only issue that day failed to build has no
  `source_bulletin`, so the problem-terrain feed leaves it out rather than
  listing it with no rules; the region sheet falls back to the page's
  selection rule in that case only, to keep showing the failed issue's
  error card.
- `examples_category` no longer reads `raw_data`, and leaves the raw
  readers listed in
  [read-paths-never-read-raw-data](read-paths-never-read-raw-data.md).
