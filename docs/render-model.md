---
name: render-model
description: Bulletin.render_model shape — danger ratings, traits, prose, the derived block, RENDER_MODEL_VERSION, enrich_render_model, fidelity guard
status: current
last-reviewed: 2026-10-31
---

# Render model

Each `Bulletin` stores a pre-computed `render_model` JSONField built at ingest time so templates contain no derivation logic.

**Shape**: `{ version, source, danger, traits[], metadata, prose, danger_patterns, derived }`.
- `danger` — `{ key, number, subdivision, ratings[] }` resolved from `dangerRatings`.
  - `ratings[]` — one entry per CAAML `dangerRating` that had a valid `mainValue`. Each entry shape: `{ period, key, subdivision, elevation }`.
    - `period` — `"all_day"`, `"earlier"`, or `"later"` (from `validTimePeriod`).
    - `key` — EAWS danger key string (`"low"` … `"very_high"`).
    - `subdivision` — SLF only: `"+"`, `"="`, `"-"`, or `None`. Always `None` for ALBINA and MeteoFrance.
    - `elevation` — projected elevation dict `{ lower, upper, treeline, treeline_side }` or `None` when the rating has no elevation bounds. `lower`/`upper` are integers or `None`; `treeline_side` records which CAAML bound carried the `"treeline"` token (`"lower"` = above treeline, `"upper"` = below treeline, or `None`).
  - **Elevation-band split** (MeteoFrance): when two or more entries share `period="all_day"` but carry distinct `key` values, the bulletin uses a Météo-France style elevation split. The Day Risk Profile panel (`_day_windows_from_rm_ratings` in `apps/public/views.py`) renders these as two separate rows with elevation captions. The calendar tile layer (`day_rating.py`) derives `min_rating`/`max_rating` from the lowest and highest band keys (v7 policy, precedence 1).
- `traits[]` — one entry per aggregation entry (SLF's `customData.CH.aggregation` verbatim; synthesised for ALBINA and Météo-France — see below); each has `{ category, time_period, title, geography, problems[], prose, danger_level, band_id, elevation }`.
  - Trait and problem ordering is taken verbatim from SLF's aggregation.
  - `category` is `"dry"` or `"wet"`, sourced directly from SLF's aggregation — not inferred.
  - `band_id` / `elevation` are set only on ALBINA traits, whose synthesised aggregation carries an elevation band per entry; `None` for SLF and Météo-France.
  - Every raw ALBINA / Météo-France problem reaches a trait (v10): one problem type described in two elevation bands within one period is two problems, each with its own elevation, aspects, rating and size. Until v10 the builder kept the last of them.
  - `geography.source` is `"problems"` when aspects/elevation are present, or `"prose_only"` when the SLF prose comment is the only geographic description.
- `metadata` — `{ publication_time, valid_from, valid_until, next_update, unscheduled, lang }`. Timestamps are ISO 8601 strings or `None`; `unscheduled` defaults to `False`; `lang` defaults to `"en"`.
- `prose` — `{ snowpack_structure, weather_review, weather_forecast, tendency[], avalanche_activity, tendency_lead }`. Scalars are HTML strings or `None`. Each tendency entry has `{ comment, tendency_type, valid_from, valid_until }`. `avalanche_activity` is always present (empty strings for SLF); `tendency_lead` is ALBINA's forecaster one-liner from `tendency[0].highlights`, `None` for the other two providers.

- `derived` (v10, SNOW-1341) — what every reader used to compute from the model on each request, computed once by the builder (`build_derived`) and stored with it:
  - `terrain_rules` — the problem-terrain rules (`terrain_rules`), read by `/api/problem-terrain/` and the region sheet's key.
  - `period_transition` — `dataclasses.asdict(PeriodTransition)` or `None`; read back with `period_transition_of`.
  - `problem_family` — `derive_problem_family`'s token.
  - `headline_inputs` — `{ source, partition_type, peak_rating, direction, family }`, the five arguments of the instance's `headline_for`. The headline copy is localised, so it is never stored.
  - `state` — the alert state, `BulletinState.to_json()` plus its `fingerprint` (the send log's idempotency key); read back with `state_of`, which recomputes a state stored under another `BULLETIN_STATE_VERSION`.
  - `danger_levels` — `{ levels, max }`: the distinct keys of `danger.ratings`, ordered up the scale from `low` to `very_high`, and the highest (`None` when there are none). Copied to the `Bulletin.danger_levels` column as `"|moderate|considerable|"`, which `/examples/<level>/` filters on.

  Readers go through `derived_of(render_model)`, which returns the stored block and computes it only for a model that has none — the version-0 sentinel a failed or stale row reads as, or a pre-v10 row the rebuild has not reached — so a reader's output never depends on which it got.

**Versioning**: `RENDER_MODEL_VERSION = 10` (in `apps/ingest/normalise/render_model.py`, the service's, since SNOW-1270). Bump it whenever the output shape or builder logic changes, and ship the bump with a `Deploy-Step: rebuild_render_models --commit` trailer (see [deployment](deployment.md)) so the stored models are rebuilt on deploy — and, on a source environment, `rebuild_source_render_models` too, so the service's stored models follow. `BulletinQuerySet.needs_render_model_rebuild()` returns all rows with a stale version. v8 removed the duplicate top-level `snowpack_structure` key (SNOW-488) — `prose.snowpack_structure` is unchanged and remains the canonical location. v9 stores SLF's `neutral` subdivision as `"="` (SNOW-1054); v8 and earlier keyed the token map on `equal`, which SLF never sends, so every neutral rating was stored as `None`. v10 adds the `derived` block and keeps every band of an ALBINA / Météo-France problem type (SNOW-1341); its Deploy-Steps are `rebuild_source_render_models --commit` then `rebuild_render_models --commit`.

**Display enrichment** (SNOW-69): the stored render model is the canonical, version-stamped shape — but it isn't what templates consume. `enrich_render_model()` in `apps/public/views.py` adds presentation-ready fields (problem labels, `ElevationBounds`, `field_guidance`, `hide_comment`) on top of each trait's problems before the data reaches the template. Both the bulletin page and the map drawer endpoint (`apps.public.api.region_summary`) call it, so the two render paths share a single enriched shape. Keep purely-derivable presentation in the enrichment step rather than the builder — it stays out of the stored JSON, doesn't bump the version, and can change without a rebuild.

**Validation**: `build_render_model` validates against the canonical 8-token EAWS problem-type enum (`DRY_PROBLEM_TYPES | WET_PROBLEM_TYPES`) and raises `RenderModelBuildError` on unknown types, aggregation/problem set mismatches, or empty `problemTypes`. Both lists empty is a legitimate quiet-day state (no raise).

**Aggregation per provider**: only SLF ships a `customData.CH.aggregation` block. For ALBINA and Météo-France the per-source adapter (`AlbinaAdapter`, `MeteoFranceAdapter`) synthesises one from `avalancheProblems`, grouping on `category × validTimePeriod` (`_synthesise_aggregation_from_problems`; ALBINA additionally groups by elevation band). Per the CAAML v6 schema, aggregation is a *display hint* rather than load-bearing data, and dry/wet problem types are disjoint — so the synthesis is unambiguous and cannot invent a grouping the provider contradicts. An SLF bulletin that has `avalancheProblems` but no aggregation is **not** synthesised: the builder logs an error and renders the bulletin with no traits, because that is an upstream data gap rather than a known provider shape.

**On validation failure**: the caller stores `render_model = {"version": 0, "error": "...", "error_type": "..."}`. `fetch_bulletins` exits non-zero via `CommandError` when `run.records_failed > 0`. `rebuild_render_models` prints a failure summary and exits non-zero.

**No rebuild on read** (SNOW-1342): `_get_render_model` in `apps/public/views.py` returns the stored model when `render_model_version` is current. A stale row is **not** rebuilt per request — it reads as the `{"version": 0, "error": …, "error_type": "StaleRenderModel"}` sentinel (a stored failure keeps its own error) and logs a warning naming both versions; the template renders the error card. That is why a version bump needs the `rebuild_render_models --commit` Deploy-Step. Read paths never read `raw_data` at all — [why](decisions/read-paths-never-read-raw-data.md).

**Day character**: `compute_day_character(render_model)` is a pure function returning a `DayCharacter` with three fields — `key` (the locale-independent cascade key: `stable`, `manageable`, `hard_to_read`, `widespread`, `dangerous`), `label` (the display string), and `explainer` (the one-line day summary). Empty `traits` → the `stable` key immediately. Branch and export on `key`, never on `label`, which varies with the active locale.

The label comes from the five-rule cascade in `_classify_day`. The explainer is bulletin-specific: `compute_day_character` derives how the day moves (`classify_movement` over `compute_period_transition`), what level it ends at, and which problems it names, then looks the combination up in the 80-cell matrix in `apps/bulletins/services/day_summary.py` — see [`docs/day-summary.md`](day-summary.md). `compute_period_transition` carries `source_number` alongside `destination_number` so a changing day can state both ends.

**Services**:
- `apps/ingest/normalise/render_model.py` — the service's interpretation (SNOW-1270): `build_render_model()`, `detect_source()`, `compute_period_transition()`, `day_character_key()`, `problem_types_for()`, `RenderModelBuildError`, `RENDER_MODEL_VERSION`, and the derived block's `build_derived()`, `derived_of()`, `period_transition_of()` and `state_of()` (SNOW-1341); exported through `apps.ingest.public`. The prose parser is `apps/ingest/normalise/prose/`; the alert state, `bulletin_state()` / `BulletinState`, is `apps/ingest/normalise/bulletin_state.py`.
- `apps/bulletins/services/render_model.py` — the instance's presentation: `compute_day_character()` (the translated label and the `day_summary` explainer for the service's key), a `detect_source()` that answers a `Bulletin.Source`, and `danger_levels_column()`, the `Bulletin.danger_levels` value for a model. Re-exports the builder names so existing callers keep their import.
- `apps/bulletins/services/day_summary.py` — `summary_for()`, `classify_movement()`, `classify_readability()`, `join_problems()`, `DAY_SUMMARY_VERSION`.
- `apps/ingest/store.py` — `store_bulletin` builds the model whenever it writes a `SourceBulletin` row, and rebuilds a stale one on an unchanged fetch; `rebuild_render_model` rebuilds one row (SNOW-1270). `SourceBulletin.render_model` is served in the bulletins feed item.
- `apps/bulletins/services/apply.py` — `upsert_bulletin` copies the service's model when it is at this code's `RENDER_MODEL_VERSION` and otherwise calls `build_render_model` inline (never via a signal); increments `run.records_failed` on `RenderModelBuildError`.

**Fidelity guard** (SNOW-671): the render model is one half of a claim — that Snowdesk shows the provider's bulletin in full rather than a simplified subset. `tests/sentinels/fidelity.py` makes that claim enforceable by holding one row for every dotted CAAML path across the nine committed sentinels, each either declared rendered (with a probe naming the surface it lands on) or excluded (with a written reason). `bin/fidelity-lint` checks that every path is classified and every exclusion is reasoned; `tests/sentinels/test_fidelity.py` renders each sentinel's page and checks that every rendered path's value actually arrives. Adding a field to the builder or a surface to the templates means adding or moving a row — the contract, the failure messages and the `--show-exclusions` audit view are in [`tests/sentinels/README.md`](../tests/sentinels/README.md). The guard exists because `metadata.unscheduled` was built, stored and versioned for the project's whole life without any template reading it (SNOW-670).
