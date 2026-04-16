# User Testing Scenarios — Render Model Feature

> **Prerequisites**
>
> 1. All three terminals running:
>    - Terminal 1: `npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch`
>    - Terminal 2: `poetry run python manage.py runserver`
>    - Terminal 3: available for management commands
> 2. Migrations applied: `poetry run python manage.py migrate`
> 3. `.env` contains `DJANGO_SETTINGS_MODULE=config.settings.development` (ensures `DEBUG=True`, which makes the day-character debug band visible at the bottom of each panel card).
> 4. Test data loaded as described in section A below. All shell snippets assume the working directory is `/Users/hugo/Projects/snowdesk-data-pipeline`.
> 5. A Django superuser exists for admin access: `poetry run python manage.py createsuperuser`.
> 6. Log output is visible in Terminal 2 (runserver stdout).

---

## A. Pre-flight Setup

### Scenario 1: Fetch today's bulletins from the live SLF API

**Goal**: Populate the database with real bulletins that have `render_model_version = 2`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In Terminal 3, run `poetry run python manage.py fetch_bulletins --date $(date +%Y-%m-%d) --commit` | Command prints `Fetching bulletins from <today> to <today>` then `Done. Run #N: X created, Y updated across 1 day(s).` |
| 2 | Open `http://localhost:8000/examples/random/` in a browser | A bulletin panel renders without errors; a danger band and at least one trait section are visible |
| 3 | Open `http://localhost:8000/admin/pipeline/bulletin/` (log in with superuser credentials) | Bulletin rows appear with recent `issued_at` dates |

**Pass**: Both the public page and admin changelist load without 500 errors.
**Fail**: Command prints an error, or either page returns an error response.

---

### Scenario 2: Load sample bulletins for specific UI test cases

**Goal**: Insert the six sample fixtures into the database so later scenarios have a stable, known bulletin to navigate to.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In Terminal 3, run `poetry run python manage.py shell` | Python shell opens |
| 2 | Paste the block below (A) and press Enter | Output ends with `Done. 6 bulletins loaded.` |
| 3 | Type `exit()` | Shell closes |

**Block A** — copy and paste as one block:

```python
import json
from django.utils import timezone
from pipeline.models import PipelineRun
from pipeline.services.data_fetcher import upsert_bulletin

run = PipelineRun.objects.create(triggered_by="qa-setup")
run.mark_running()

samples = [
    "sample_data/sample_variable_day.json",
    "sample_data/sample_stable_day.json",
    "sample_data/sample_subdivision_3plus_day.json",
    "sample_data/sample_prose_only_day.json",
    "sample_data/sample_no_aggregation_day.json",
    "sample_data/sample_danger_rating_low.json",
]
for path in samples:
    with open(path) as f:
        data = json.load(f)
    props = data["properties"]
    upsert_bulletin(props, run)
    print(f"  loaded {props['bulletinID']}")

run.mark_success(records_created=6, records_updated=0)
print("Done. 6 bulletins loaded.")
```

**Pass**: All six bulletin IDs are printed with no exceptions.
**Fail**: Any `KeyError`, `IntegrityError`, or traceback.

> **Note on region slugs**: `upsert_bulletin` auto-creates Region rows. Verify they appear at `http://localhost:8000/admin/pipeline/region/` after loading.

---

## B. Happy-Path Scenarios

### Scenario 3: Variable day — two trait sections render (dry + wet)

**Goal**: Confirm the bulletin card splits dry/wet problems into separate labelled trait sections with correct categories and time periods.

**Preconditions**: Scenario 2 completed; bulletin `27f53363-ab17-4e1d-9367-24d68633b03d` is in the database, linked to region `CH-1213`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/CH-1213/hohgant/2026-04-09/` | Page loads; header shows "Hohgant" and date "Thu 9 Apr 2026" |
| 2 | Inspect the panel card body | Two visually distinct trait sections are present, one above the other |
| 3 | Read the first trait header | Shows title "Dry avalanches, whole day", a badge labelled "dry", and no time-period badge (because `time_period == "all_day"` is suppressed) |
| 4 | Read the second trait header | Shows title "Wet-snow and gliding avalanches, as the day progresses", a badge labelled "wet", and a badge labelled "later" |
| 5 | Check the first trait body | One problem row: "No distinct problem" icon and label; summary text references N/NE/NW aspects above 1800m |
| 6 | Check the second trait body | Two problem rows: "Wet snow" and "Gliding snow"; both share the same SLF comment; duplicate-comment hiding applies within this trait — the comment appears once |
| 7 | Scroll to bottom of card | Debug band reads "Day character: Hard-to-read day" (DEBUG=True required) |

**Pass**: Two separate trait headers visible with correct category badges; day character label is "Hard-to-read day".
**Fail**: Only one trait section renders, a trait header is missing a badge, or day character reads anything other than "Hard-to-read day".

---

### Scenario 4: Stable day — single trait renders

**Goal**: Confirm a bulletin with one dry/all_day aggregation entry renders a single trait section without a time-period badge.

**Preconditions**: Scenario 2 completed; bulletin `stable-day-001` in the database, linked to region `CH-4115`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/CH-4115/piz-buin/2026-01-11/` | Page loads; header shows "Piz Buin" |
| 2 | Inspect the panel card body | Exactly one trait section is visible |
| 3 | Read the trait header | Title "Dry avalanches, whole day", category badge "dry"; no time-period badge |
| 4 | Check the trait body | One problem row: "No distinct problem" label; no aspect rose; no elevation icon |
| 5 | Check the debug band | "Day character: Stable day" |

**Pass**: Single trait, no time-period badge, day character "Stable day".
**Fail**: Multiple traits rendered, time-period badge appears, or day character is wrong.

---

### Scenario 5: Level-3+ subdivision shows "Widespread danger" day character

**Preconditions**: Scenario 2 completed; bulletin `subdivision-3plus-001` in the database, linked to region `CH-4115`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/CH-4115/piz-buin/2026-02-06/` | Page loads; header shows "Piz Buin" |
| 2 | Read the danger band | Shows "Considerable (3+)" — the `+` subdivision suffix appears after the danger number |
| 3 | Inspect the trait body | One problem row: "Wind slab", N/NE aspects, above 2600m |
| 4 | Check the debug band | "Day character: Widespread danger" |

**Pass**: Danger band shows `3+`; day character reads "Widespread danger".
**Fail**: `+` suffix absent, or day character reads anything else.

---

### Scenario 6: Prose-only trait renders SLF comment block instead of per-problem panels

**Preconditions**: Scenario 2 completed; bulletin `prose-only-001` in the database, linked to region `CH-4115`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/CH-4115/piz-buin/2026-03-11/` | Page loads; header shows "Piz Buin" |
| 2 | Inspect the trait section body | No problem tag row (no icon, no aspect rose, no elevation indicator); instead, a paragraph of prose text is shown |
| 3 | Read the prose text | Matches the comment: "Isolated wet slab releases are possible below 2000m on sunny aspects. The hazard is low overall." |
| 4 | Check for attribution line below the prose | Right-aligned line reads "SLF Bulletin" |
| 5 | Check the debug band | "Day character: Stable day" |

**Pass**: No problem-tag row visible; prose paragraph shown with "SLF Bulletin" attribution.
**Fail**: A problem row tag renders, or the prose text is absent.

---

## C. Management Command Scenarios

### Scenario 7: Default `rebuild_render_models` is read-only and reports counts

**Preconditions**: At least one bulletin exists in the database.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Run `poetry run python manage.py rebuild_render_models` | Prints heading `Rebuilding render models (version=N) [READ-ONLY]` |
| 2 | Read "Bulletins to process:" line | Shows `0` (all current bulletins fresh) and "Nothing to do." |
| 3 | Manually set one bulletin's version to 0: `poetry run python manage.py shell -c "from pipeline.models import Bulletin; Bulletin.objects.filter(bulletin_id='stable-day-001').update(render_model_version=0)"` | Exits without error |
| 4 | Re-run `poetry run python manage.py rebuild_render_models` | "Bulletins to process: 1" and "Read-only run complete — would have rebuilt 1 bulletin(s)… Pass --commit to persist." |
| 5 | Shell check: `Bulletin.objects.get(bulletin_id="stable-day-001").render_model_version` | Prints `0` — version was not updated |

**Pass**: Output says "would have rebuilt"; version remains 0.
**Fail**: Version changes in the database, or the command errors.

---

### Scenario 8: `--commit` persists; default run only processes stale rows

**Preconditions**: `stable-day-001` has `render_model_version=0`. Other sample bulletins at the current `RENDER_MODEL_VERSION`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Run `poetry run python manage.py rebuild_render_models --commit` | Heading without `[READ-ONLY]` |
| 2 | Read count line | "Bulletins to process: 1" |
| 3 | Read completion | "Rebuilt 1 bulletin(s), 0 failed." |
| 4 | Shell check: `Bulletin.objects.get(bulletin_id="stable-day-001").render_model_version` | Prints the current `RENDER_MODEL_VERSION` |
| 5 | Re-run `poetry run python manage.py rebuild_render_models --commit` | "Bulletins to process: 0" and "Nothing to do." |

**Pass**: Only stale row touched; second run finds nothing.

---

### Scenario 9: `rebuild_render_models --all --commit` rewrites every row

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Shell: `Bulletin.objects.count()` | Prints N > 0 |
| 2 | Run `poetry run python manage.py rebuild_render_models --all --commit` | Heading `[ALL]` (no `[READ-ONLY]`) |
| 3 | Count line | "Bulletins to process: N" matches step 1 |
| 4 | Completion | "Rebuilt N bulletin(s), 0 failed." |
| 5 | Admin: bulletin `updated_at` | Shows fresh timestamp |

**Pass**: All N rows processed without errors.

---

### Scenario 10: `--bulletin-id` targets one bulletin; unknown ID errors

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Run `poetry run python manage.py rebuild_render_models --bulletin-id stable-day-001 --commit` | Heading `[bulletin-id=stable-day-001]` |
| 2 | Read output | "Bulletins to process: 1" then "Rebuilt 1 bulletin(s), 0 failed." |
| 3 | Run `poetry run python manage.py rebuild_render_models --bulletin-id does-not-exist-999` | Non-zero exit with error (no `--commit` needed — id is validated up-front) |
| 4 | Read error | Contains `No bulletin found with bulletin_id='does-not-exist-999'` |

**Pass**: Single-bulletin run succeeds; unknown ID raises CommandError.

---

## D. Safety Net

### Scenario 11: Stale bulletin (version 0) still renders; warning logged

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Shell: `Bulletin.objects.filter(bulletin_id="prose-only-001").update(render_model_version=0)` | No error |
| 2 | Note current Terminal 2 log position | Known point |
| 3 | Navigate to `http://localhost:8000/CH-4115/piz-buin/2026-03-11/` | Page renders normally (matches Scenario 6) |
| 4 | Check Terminal 2 log | WARNING: `Bulletin prose-only-001 has stale render_model (version=0); building on the fly` |
| 5 | Debug band | "Day character: Stable day" |

**Pass**: Page renders correctly AND warning appears in the server log.
**Fail**: 500 error, blank content, or no warning logged.

---

## E. Edge Cases

### Scenario 12: Missing `customData.CH.aggregation` renders as single synthetic trait

**Preconditions**: Bulletin `no-aggregation-001` in database, region `CH-4115`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/CH-4115/piz-buin/2026-03-02/` | Page loads |
| 2 | Panel body | One trait section visible |
| 3 | Trait header | Category "dry"; no time-period badge; title blank → falls back to "Avalanche problem" |
| 4 | Problem row | "New snow" label, N/NE/NW aspects, "above 2000m" |
| 5 | Debug band | "Day character: Manageable day" |

**Pass**: Single synthetic trait renders correctly.

---

### Scenario 13: Same problem type across dry/wet at different periods

**Preconditions**: Variable-day sample (`27f53363-ab17-4e1d-9367-24d68633b03d`) loaded.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Shell: `b = Bulletin.objects.get(bulletin_id="27f53363-ab17-4e1d-9367-24d68633b03d"); print([t["time_period"] for t in b.render_model["traits"]])` | `['all_day', 'later']` |
| 2 | `print([p["problem_type"] for p in b.render_model["traits"][0]["problems"]])` | `['no_distinct_avalanche_problem']` only |
| 3 | `print([p["problem_type"] for p in b.render_model["traits"][1]["problems"]])` | `['wet_snow', 'gliding_snow']` |
| 4 | Visual check at `/CH-1213/hohgant/2026-04-09/` | Dry trait shows only "No distinct problem"; wet trait shows "Wet snow" + "Gliding snow" |

**Pass**: Problems appear only in the trait matching their `validTimePeriod`.

---

### Scenario 14: Admin changelist and detail view

**Preconditions**: Superuser logged in.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/admin/pipeline/bulletin/` | Changelist loads (render_model_version NOT a column — expected) |
| 2 | Click `stable-day-001` row | Detail form opens |
| 3 | Scroll through fields | `Render model` (JSON textarea) and `Render model version` (number input) present as editable fields |
| 4 | Version field value | Shows `1` (or `0` depending on test state) |
| 5 | Render model textarea | Multi-line JSON beginning `{"version": 1, "danger": ...` |
| 6 | Do NOT save | Navigate away |

**Pass**: Both fields visible.
**Note**: `render_model` is a plain JSON textarea, not syntax-highlighted (unlike `raw_data_pretty`). This is expected.

---

## F. Regression Checks

### Scenario 15: `/random/` redirect still works

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/random/` | 301 redirect to `/examples/random/` |
| 2 | Terminal 2 log | WARNING: `Deprecated URL /random/ accessed — use /examples/random/ instead` |
| 3 | Destination page | Panel card renders with at least one trait section |

---

### Scenario 16: Season bulletins listing still works

**Preconditions**: Region `CH-4115` has bulletins loaded.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/CH-4115/season/` | Page loads |
| 2 | Count line | "N bulletin(s) this season" matches loaded dates |
| 3 | Scroll panels | Each has danger band, date bar, trait section; no blank cards |
| 4 | Terminal 2 | Clean — no 500 errors |

---

### Scenario 17: Subscription flow unaffected

**Preconditions**: `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` in `.env`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/subscribe/` | Form with email field renders |
| 2 | Submit `qa-test@example.com` | Redirects to `/subscribe/sent/` |
| 3 | Find email in Terminal 2 | URL beginning `http://localhost:8000/subscribe/verify/?token=` |
| 4 | Paste verify URL | New subscriber → redirects to `/subscribe/regions/` |
| 5 | Regions page | Search field and region list visible |

---

### Scenario 18: Duplicate-comment behaviour change — per-trait, not global

**Background**: Previously, `hide_comment` was computed globally across all problems. Now it is computed per-trait. If dry and wet traits happen to contain problems with identical comment text, both traits will display the comment — intended new behaviour.

**Preconditions**: Variable-day bulletin loaded.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `/CH-1213/hohgant/2026-04-09/` | Page loads |
| 2 | Dry trait: "No distinct problem" row | Its own comment visible |
| 3 | Wet trait: "Wet snow" row | Comment hidden (`hide_comment=True`) — identical text shows under "Gliding snow" within same trait |
| 4 | Wet trait: "Gliding snow" row | Full shared comment visible |
| 5 | Confirm cross-trait independence: dry trait comment still shows even though a different comment is also present in wet trait | Dry trait shows its own comment |

**Pass**: Within-trait dedupe works; cross-trait independence preserved.
**Fail**: "Gliding snow" comment hidden (stale global suppression), or dry trait comment missing.

---

---

## F. Error state (render_model version = 0)

### Scenario: bulletin with stored error state renders error card

**Goal**: Confirm the panel shows a sympathetic error card rather than crashing when `render_model.version == 0`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Open `http://localhost:8000/admin/pipeline/bulletin/` and find any bulletin | Admin changelist loads |
| 2 | Click on a bulletin, set `render_model` to `{"version": 0, "error": "Synthetic test error", "error_type": "RenderModelBuildError"}` and save | Admin saves successfully |
| 3 | Navigate to the public page for that bulletin's region | Panel renders with a red-bordered error card containing "Bulletin data could not be processed" |
| 4 | As a non-staff user, confirm the error message is generic ("please report") | Technical error text is NOT visible |
| 5 | As a staff user, confirm the error text is shown verbatim | "Synthetic test error" appears in the card |

**Pass**: Page returns 200; error card renders; no traceback in the browser.
**Fail**: 500 error, or the bulletin panel renders as if it had traits.

---

## Relevant source files

- `pipeline/services/render_model.py` — builder + `compute_day_character` + `RenderModelBuildError`
- `pipeline/management/commands/rebuild_render_models.py` — management command
- `public/views.py` — `_build_panel_context`, `_get_render_model`, `_enrich_render_model`, safety net
- `public/templates/public/_bulletin_panel.html` — trait rendering, error card, empty-traits card, prose-only branch
- `pipeline/admin.py` — BulletinAdmin (render_model_version is editable, not read-only)
- `sample_data/sample_variable_day.json`, `sample_stable_day.json`, `sample_subdivision_3plus_day.json`, `sample_prose_only_day.json`, `sample_no_aggregation_day.json`, `sample_unknown_problem_type.json`
