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
  field (visible in the admin) and in the seeding migration's
  docstring; the flag name lives forever and tickets get squash-merged
  out of git history.

---

## Current flag inventory

| Name | Targeting (default) | Gates | Introduced |
|------|---------------------|-------|------------|
| `edit_map` | `superusers=True` | The in-map resort editor at `/map/?edit=resorts` and its API endpoints (`/api/edit/resorts/queue/`, `/api/edit/resorts/<id>/coords/`). | SNOW-86 (test case for the mechanism); first consumer is SNOW-74. |

Keep this table up to date as new flags land.

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
2. **Add a data migration** under the relevant app's `migrations/`
   directory. Pattern (modelled on
   `pipeline/migrations/0017_seed_edit_map_flag.py`):

   ```python
   from django.db import migrations

   FLAG_NAME = "<your_flag_name>"
   FLAG_NOTE = "Short prose describing what the flag gates and the SNOW ticket."


   def seed(apps, schema_editor):
       Flag = apps.get_model("waffle", "Flag")
       Flag.objects.get_or_create(
           name=FLAG_NAME,
           defaults={
               "superusers": True,  # or staff=True, authenticated=True, etc.
               "note": FLAG_NOTE,
           },
       )


   def remove(apps, schema_editor):
       Flag = apps.get_model("waffle", "Flag")
       Flag.objects.filter(name=FLAG_NAME).delete()


   class Migration(migrations.Migration):
       dependencies = [
           ("<app>", "<previous_migration>"),
           ("waffle", "0004_update_everyone_nullbooleanfield"),
       ]
       operations = [migrations.RunPython(seed, reverse_code=remove)]
   ```

   `get_or_create` keeps the migration idempotent and means an operator
   tweaking the flag in the admin won't have their changes clobbered by
   a re-run.

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

## Local-development shortcut: `?dwf_<flag>=…`

`config/settings/development.py` enables `WAFFLE_OVERRIDE = True`,
which lets you force a flag's value for the current request via
querystring:

* `https://localhost:8000/map/?edit=resorts&dwf_edit_map=1` — flag
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
