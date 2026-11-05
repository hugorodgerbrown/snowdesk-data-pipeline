---
name: public-splits-by-surface
description: apps/public splits into design_system and map; bulletin_panels first; URL and template names stay; allowed imports
status: current
last-reviewed: 2026-11-05
---

# apps/public splits by surface, not by layer (SNOW-1409)

**Decision.** `apps/public` (28.9k lines of Python, no models) splits into
three apps, one surface each, in this order:

1. **Untangle `views.py` first.** The bulletin render layer becomes
   `apps/public/bulletin_panels.py` and the location-weather context
   becomes `apps/public/weather_points.py`, each with a public API
   (SNOW-1426).
2. **`apps/design_system`** takes the staff component library: design
   tokens, fixtures, previews, the token checks and the `/_components/`,
   `/_icon-sets/` and `/_errors/` pages (SNOW-1427).
3. **`apps/map`** takes the map page, the map JSON endpoints, the in-map
   editor and the region and place sheets (SNOW-1428).

The bulletin, resort and weather pages, the site shell and the shared
template tags stay in `apps/public`.

**Why.** Three reasons:

- **Surface is the seam the code already has.** The component library
  reads the domain apps, and nothing reads it. The map page's context
  builders are called only by `map_page`. Splitting by layer (views, API,
  services) would cut through every surface instead.
- **The render layer has to come first.** The map sheets, `api.py`,
  favourites and the MCP server import underscore-private helpers from
  `views.py`. A map app cut before step 1 would import apps/public's
  privates, so the boundary would exist in name only.
- **The database is not involved.** apps/public has no models, so the
  split moves code only. There are no table pins and no ContentType rows
  to carry across, unlike [`weather-is-its-own-app`](weather-is-its-own-app.md).

**What does not move, and why.**

- **URL names.** `apps/public/urls.py` and `api_urls.py` stay the routing
  hub and import the moved views. That keeps every `public:` and `api:`
  name (165 reverses, plus paths in `static/js`). It also keeps the
  `<region_id>/` catch-all last in one file. Two includes cannot share a
  namespace, so moving the routes would mean renaming them.
- **Template names.** Templates move to the owning app's
  `templates/public/…`, and `APP_DIRS` resolves them unchanged. A partial
  that both a page and a sheet render stays in apps/public, for example
  `_region_body.html` and `_resort_body.html`.
- **Shared template tags**, including `map_focus`. Accounts, trips,
  favourites, routes and observations templates load them.

**Allowed imports.** Each new app gets a boundary test in the style of
`tests/ingest/test_boundary.py`:

| From | May import | Only via |
|---|---|---|
| `apps/public/urls.py` | `apps.design_system` | `views` |
| `apps/public/urls.py`, `api_urls.py`, `config/urls.py` | `apps.map` | its view modules |
| `apps.map` | `apps.public` | `bulletin_panels`, `weather_points`, `decorators`, `errors` |

Nothing else in `apps/` or `config/` imports either new app.

**Consequences.**

- Each step is a pure move in its own PR. The URL snapshot test added in
  step 1 must pass unchanged at every step.
- New map code goes in `apps/map`, and new component-library code goes in
  `apps/design_system`. A map module that needs a bulletin helper
  imports it from `bulletin_panels`, or adds it there; it never imports
  from `apps.public.views`.
- `design_system` is the working name. SNOW-1427 confirms it.
- **Left for later, not in this split:** moving the shared tags to
  `apps/core`, and a fourth cut of the ~14k lines that remain in
  apps/public. Either needs its own ticket.
