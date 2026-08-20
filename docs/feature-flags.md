---
name: feature-flags
description: django-waffle operator guide — Flag/Switch/Sample, flag inventory, waffle_flags.json manifest, sync_waffle_flags command
status: current
last-reviewed: 2026-08-05
---

# Feature flags (django-waffle)

Snowdesk uses [django-waffle](https://waffle.readthedocs.io/) for
feature flagging. A flag turns a code path on for a targeted slice of
users (you, every superuser, every authenticated user, a specific
group, a percentage of traffic, …) without a deploy. This is the
primary mechanism for previewing site-admin tooling and not-yet-public
features on the live site.

This doc is the operator's reference. The gate is checked **server-side**
in views and templates; we don't expose a `wafflejs` endpoint.

---

## When to reach for what

Waffle ships three primitives. Pick the smallest one that fits.

| Primitive | Use it for | Storage shape |
|-----------|-----------|---------------|
| **Flag**    | Per-request decisions that depend on _who_ is making the request — superusers, staff, named users, groups, percentages of traffic. | DB row, edited at `/admin/waffle/flag/`. |
| **Switch**  | Global on/off kill switches — same answer for every request, no targeting needed. ("Disable bulk email.") | DB row, edited at `/admin/waffle/switch/`. |
| **Sample**  | Random-percentage sampling — gives the same fixed probability of `True` for every request, used for load-shedding or canary rollouts that don't need to be sticky per user. | DB row, edited at `/admin/waffle/sample/`. |

If you're not sure: use a **Flag**. The other two are conveniences.

---

## Naming convention

* `snake_case`. Lowercase, underscores between words. The waffle admin
  is searchable by name; readable names beat clever ones.
* Broad over narrow. Prefer one flag that covers a feature surface
  (`edit_map`) over many sibling flags (`edit_map_resorts`,
  `edit_map_regions`) until you actually need different sub-scopes.
* No `SNOW-XX` in the name. Reference the ticket in the flag's `note`
  field in the manifest (it is copied to the DB row and is visible in
  the admin); the flag name lives forever and tickets get squash-merged
  out of git history.

---

## Current flag inventory

| Name | Targeting (default) | Gates | Introduced |
|------|---------------------|-------|------------|
| `edit_map` | `superusers=True` | The in-map resort editor at `/?edit=resorts` and its API endpoints (`/api/edit/resorts/queue/`, `/api/edit/resorts/<id>/save/`, `/api/edit/resorts/create/`). | SNOW-86 (test case for the mechanism); first consumer is SNOW-74. |
| `routes` | `superusers=True` | The GPX routes panel and its upload/list/rename/delete endpoints under `/routes/`, and the map route overlay (`routes.geojson` + the line layers). | SNOW-686, SNOW-687. |
| `slope_layer` | **`everyone=True`** | The map's "Terrain" section and its "Slope angle" row — the swisstopo slope-angle raster (`ch.swisstopo.hangneigung-ueber_30`), its coverage outline and its legend key. | SNOW-691. |
| `sync_log` | `superusers=True` | The manage-page "Sync log" panel (reads `window.pwaDb.getSyncLog()` via `static/js/sync_log.js`) and its matching `/help/` section. | SNOW-482. |
| `weather_layer` | `superusers=True` | The map's "Weather" overlay (condition symbols + temperature at forecast points), `/api/forecast-weather.geojson`, the `days` property on `favourites.geojson`, and the matching `/help/` section. | SNOW-573. |

`slope_layer` is the only flag here that is **not** a rollout gate.
It ships `everyone=True`, so the overlay is on for every visitor from the
first deploy that runs `sync_waffle_flags --commit`; the flag exists as a
kill switch, because the layer depends on a third-party tile service we do
not operate. Setting `Everyone: No` in the admin removes the row, the
raster and the legend key without a deploy — which is the lever to reach
for if swisstopo's service degrades, or if the licence question recorded in
the flag's `note` resolves against us.

The saved-map-pin favourites feature (SNOW-413), the field-report button
and submission endpoints (SNOW-324), the "Community reports" read overlay
(SNOW-419), and the `/observations/` page (SNOW-476) were all pre-launch
flags — removed once the features reached general availability (SNOW-520).
They remain gated by ordinary auth/verification/ownership checks, just not
by a waffle flag.

The resort page's field-observations panel is point-local, scoped to the
configurable `FIELD_OBSERVATION_RADIUS_KM` setting (default 10 km) around
the resort's own coordinates, with a region-wide fallback for resorts
missing coordinates (SNOW-508).

Keep this table up to date as new flags land. The **source of truth for
which flags exist** is `apps/core/fixtures/waffle_flags.json` (SNOW-502) — the
`sync_waffle_flags` management command reconciles the DB to that manifest
on every deploy; this table is the human-readable summary of the same set.

**Adding a flag means adding a manifest entry — nothing else.** Never seed
a `waffle.Flag` row from a data migration. `sync_waffle_flags` runs on
every deploy and deletes any DB row the manifest does not name, so a
migration-seeded flag is created and then destroyed in the same build, and
the feature it gates goes dark with `WAFFLE_FLAG_DEFAULT = False`. SNOW-685
did exactly this and the routes feature was invisible on staging until the
manifest entry landed.

---

## How to toggle a flag on the live site

1. Log in to `/admin/` as a Django superuser.
2. Open `/admin/waffle/flag/`.
3. Click the flag.
4. Pick one or more targeting rules:
   * **Superusers** — every superuser. Simplest "just for me" knob.
   * **Staff** — every staff user.
   * **Authenticated** — every logged-in user.
   * **Users** (M2M) — list specific Django users. Use this to invite a
     non-superuser teammate to a feature.
   * **Groups** (M2M) — every member of a Django auth Group.
   * **Percent** — a sticky-per-request percentage (waffle stores a
     cookie so the same visitor sees the same answer across requests).
   * **Everyone** — three-state. `Yes` overrides every other rule and
     turns the flag on for **all** requests. `No` is the kill switch:
     overrides every rule and turns the flag off for everybody. `Unknown`
     (the default) means "fall through to the rules above."
5. Save.

The change takes effect on the next request — there's no cache to bust.

> **Killing a feature live** — set `Everyone = No` rather than untiking
> `Superusers`. It's a single, reversible knob and reads as
> "intentional kill" in the admin's audit log.

---

## How to add a new flag

1. **Pick a name** following the convention above.
2. **Add an entry** to `apps/core/fixtures/waffle_flags.json` (SNOW-502):

   ```json
   {
     "name": "your_flag_name",
     "note": "Short prose describing what the flag gates and the SNOW ticket.",
     "superusers": true
   }
   ```

   `name` and `note` are required; the rest of the ``Flag`` create-time
   defaults (`staff`, `authenticated`, `everyone`, `percent`, `testing`,
   `rollout`) are optional — omit a key to fall back to the `Flag` model's
   own default. An unrecognised key (e.g. a `superuser` typo) fails the
   next `sync_waffle_flags` run rather than being silently ignored. The
   next deploy's `sync_waffle_flags --commit` (run from `build.sh`) creates
   the row; re-running against a DB that already has it is a no-op —
   an operator tweaking the flag's targeting in the admin afterwards is
   never clobbered, because the command only creates and deletes, never
   edits an existing row in place.

3. **Gate the code path.** Server-side flag check:

   ```python
   import waffle

   if waffle.flag_is_active(request, "your_flag_name"):
       ...
   ```

   In a template:

   ```django
   {% load waffle_tags %}
   {% flag "your_flag_name" %}
       ...gated markup...
   {% endflag %}
   ```

4. **Test both states.** Use the `override_flag` testutil rather than
   the `WAFFLE_FLAG_DEFAULT` setting:

   ```python
   from waffle.testutils import override_flag

   @override_flag("your_flag_name", active=True)
   def test_feature_is_visible_when_flag_on(client): ...

   @override_flag("your_flag_name", active=False)
   def test_feature_is_404_when_flag_off(client): ...
   ```

   `override_flag` works as both a method decorator and a class
   decorator; class-level decoration is cleanest when every test in a
   class needs the same flag state. See
   `tests/public/test_edit_resorts_api.py` for examples.

5. **Add a row** to the inventory table above.

6. **Update `CLAUDE.md`** if the flag is the gate for a major feature
   surface — the feature-specific reference table should mention it.

---

## How to remove a flag

1. **Remove the manifest entry** from `apps/core/fixtures/waffle_flags.json`.
2. **Strip every `{% flag %}` / `flag_is_active()` call site** that gates
   on the flag, in the **same commit** — a leftover call site fails
   closed (`WAFFLE_FLAG_DEFAULT = False`) once the row is gone, which
   silently disables the feature rather than raising an error, so don't
   rely on that as a substitute for removing the call site.
3. Ship it. The next deploy's `sync_waffle_flags --commit` deletes the
   `Flag` row — the DB is reconciled to the manifest by create + delete,
   so an entry no longer in the manifest is removed automatically with
   no separate cleanup step.

---

## Local-development shortcut: `?dwf_<flag>=…`

`config/settings/development.py` enables `WAFFLE_OVERRIDE = True`,
which lets you force a flag's value for the current request via
querystring:

* `https://localhost:8000/?edit=resorts&dwf_edit_map=1` — flag
  forced **on** for this request, regardless of the DB row.
* `…&dwf_edit_map=0` — forced **off**.

Production deliberately omits this — an externally toggleable override
would defeat the point of the gate.

---

## Settings reference

| Setting | Where | Value | Why |
|---------|-------|-------|-----|
| `WAFFLE_FLAG_DEFAULT` | `base.py` | `False` | A flag with no DB row evaluates **off**. Typos in `flag_is_active(...)` calls fail closed instead of silently exposing the gated path. |
| `WAFFLE_CREATE_MISSING_FLAGS` | `base.py` | `False` | Looking up an unknown flag must not auto-create it. Flag rows are intentional config; we want them to land via migration / admin so reviewers see them in the diff. |
| `WAFFLE_OVERRIDE` | `development.py` only | `True` | Enables the `?dwf_<flag>=…` querystring override. Off everywhere else. |
| `waffle.middleware.WaffleMiddleware` | `MIDDLEWARE` (after `AuthenticationMiddleware`) | — | Required by waffle to attach `request.waffles` and pick up `request.user` for per-user targeting. |
| `"waffle"` | `INSTALLED_APPS` | — | Provides the `Flag` / `Switch` / `Sample` models, admin, template tags, and `flag_is_active` API. |

---

## Why no `wafflejs` endpoint?

`waffle.urls` exposes a `/wafflejs/` view that emits the current user's
flag values as a tiny JS module so client-side code can branch on them.
We haven't mounted it yet — every gated feature so far is a
server-rendered page or a JSON endpoint, both of which check the flag
on the server.

If a future feature needs JS-side flag checks, mount waffle's URL conf
in `config/urls.py`:

```python
path("waffle/", include("waffle.urls")),
```

…and load `<script src="{% url 'wafflejs' %}"></script>` from the
relevant template.
