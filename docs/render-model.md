# Render model

Each `Bulletin` stores a pre-computed `render_model` JSONField built at ingest time so templates contain no derivation logic.

**Shape**: `{ version, danger, traits[], fallback_key_message, snowpack_structure, metadata, prose }`.
- `danger` — `{ key, number, subdivision }` resolved from `dangerRatings`.
- `traits[]` — one entry per `customData.CH.aggregation` entry; each has `{ category, time_period, title, geography, problems[], prose, danger_level }`.
  - Trait and problem ordering is taken verbatim from SLF's aggregation.
  - `category` is `"dry"` or `"wet"`, sourced directly from SLF's aggregation — not inferred.
  - `geography.source` is `"problems"` when aspects/elevation are present, or `"prose_only"` when the SLF prose comment is the only geographic description.
- `metadata` — `{ publication_time, valid_from, valid_until, next_update, unscheduled, lang }`. Timestamps are ISO 8601 strings or `None`; `unscheduled` defaults to `False`; `lang` defaults to `"en"`.
- `prose` — `{ snowpack_structure, weather_review, weather_forecast, tendency[] }`. Scalars are HTML strings or `None`. Each tendency entry has `{ comment, tendency_type, valid_from, valid_until }`.
- `snowpack_structure` (top-level) is kept alongside `prose.snowpack_structure` for backward compatibility; both hold the same value. Will be dropped in v4.

**Versioning**: `RENDER_MODEL_VERSION = 3` (in `pipeline/services/render_model.py`). Bump it and run `rebuild_render_models` whenever the output shape or builder logic changes. `BulletinQuerySet.needs_render_model_rebuild()` returns all rows with a stale version.

**Validation**: `build_render_model` validates against the canonical 8-token EAWS problem-type enum (`DRY_PROBLEM_TYPES | WET_PROBLEM_TYPES`) and raises `RenderModelBuildError` on unknown types, aggregation/problem set mismatches, or empty `problemTypes`. Both lists empty is a legitimate quiet-day state (no raise).

**Missing aggregation is tolerated**: when a bulletin has `avalancheProblems` but no `customData.CH.aggregation`, the builder synthesises aggregation from the problem types (grouping on `category × validTimePeriod`) rather than failing. Per the CAAML schema and our analysis (see memory: `project_aggregation_purpose.md`, `project_dry_wet_disjoint_problem_types.md`), aggregation is a display hint and dry/wet problem types are disjoint, so the synthesis is unambiguous. A warning is logged so operators can spot the upstream gap.

**On validation failure**: the caller stores `render_model = {"version": 0, "error": "...", "error_type": "..."}`. `fetch_bulletins` exits non-zero via `CommandError` when `run.records_failed > 0`. `rebuild_render_models` prints a failure summary and exits non-zero.

**Safety net**: `_get_render_model` in `public/views.py` detects a stale `render_model_version` at render time, rebuilds on the fly, and logs a warning. On `RenderModelBuildError` during the rebuild it returns an error sentinel dict (does NOT write to DB); the template renders an error card. This keeps the page functional during a backfill; the warning is the signal to run the rebuild command.

**Day character**: `compute_day_character(render_model)` is a pure function that classifies a render model into one of five labels (`"Stable day"`, `"Manageable day"`, `"Hard-to-read day"`, `"Widespread danger"`, `"Dangerous conditions"`). Empty `traits` → `"Stable day"` immediately.

**Services**:
- `pipeline/services/render_model.py` — `build_render_model()`, `compute_day_character()`, `RenderModelBuildError`, `RENDER_MODEL_VERSION`.
- `pipeline/services/data_fetcher.py` — `upsert_bulletin` calls `build_render_model` inline (never via a signal); increments `run.records_failed` on `RenderModelBuildError`.
