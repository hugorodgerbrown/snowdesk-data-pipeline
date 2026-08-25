---
name: site-structure
description: Public route map — home is the map page, bulletin/resort/favourites/observations URLs, HTMX partial prefixes, retired AI-summary design
status: current
last-reviewed: 2026-08-25
---

# Snowdesk site structure

The public URL surface, as routed. Route tables live in
[`config/urls.py`](../config/urls.py) (project level) and
[`apps/public/urls.py`](../apps/public/urls.py),
[`apps/accounts/urls.py`](../apps/accounts/urls.py),
[`apps/favourites/urls.py`](../apps/favourites/urls.py) and
[`apps/observations/urls.py`](../apps/observations/urls.py) — this page is the
map of them, not a second source of truth.

## Route ordering constraint

`apps.public.urls` is included at the project root (`""`), and its last three
patterns match `<region_id:region_id>/…`. That generic pattern would swallow
any single-segment path registered after it, so **every literal public route
must be registered before it**. The `/map/` redirect and the `partials/…`
prefixes depend on this ordering — don't reorder
(see [`docs/calendar.md`](calendar.md) and [`docs/map-and-api.md`](map-and-api.md)).

## Full-page routes

| Route | View | Notes |
|-------|------|-------|
| `/` | `public.views.home` | **The interactive map.** Not a marketing landing — the map is the homepage (SNOW-314), with a dismissable `#home-intro` overlay on first visit. CH-4115 (Martigny/Verbier) is pre-selected so the readout chip and breadcrumb are correct on first paint (SNOW-342). `?edit=resorts` opens the resort-coordinate editor for a superuser (SNOW-74/86; SNOW-724 replaced its waffle flag with the equivalent Django check). |
| `/map/` | redirect | Permanent 301 to `/`, query string forwarded, kept for old bookmarks (SNOW-344). |
| `/<region_id>/` | `bulletin_detail` | Today's bulletin for a region, never redirecting away. |
| `/<region_id>/<slug>/` | `bulletin_detail` | Slugged form, the canonical shareable URL. |
| `/<region_id>/<slug>/<date_str>/` | `bulletin_detail` | A specific day. Past days are immutable. |
| `/resorts/<resort_id>/<slug>/` | `resort_detail` | Resort page — region weather snapshot plus a point-local field-observations panel. |
| `/favourites/<uuid>/` | `favourites.views.favourite_detail` | A saved map pin or resort. |
| `/observations/` | `public.views.observations_list` | Community field reports. |
| `/how-to-read-a-bulletin/` | `how_to_read_bulletin` | Domain primer for readers. |
| `/help/` | `help_page` | Help, including the PWA/offline sections. |
| `/examples/random/` | `examples_random` | A sample bulletin; `/random/` is a deprecated redirect to it. |
| `/examples/category/<danger_level>/` | `examples_category` | A sample bulletin at a given danger level. |
| `/s/<token>/` | `share_redirect` | Short share links. |
| `/privacy/`, `/terms/`, `/terms-of-service/`, `/colophon/` | static pages | Legal and attribution. |

Account routes (`/account/…` — register, verify, setup, sign-in, manage,
change-email, unsubscribe, WebAuthn, push) are documented in
[`docs/accounts.md`](accounts.md).

## Staff-only routes

Mounted unconditionally and **staff-gated in the view** — these are not behind
a `DEBUG` check, so they exist in production and must stay staff-only.

| Route | Purpose |
|-------|---------|
| `/_components/` | Component library — the canonical design-system reference ([`docs/design-system.md`](design-system.md)). |
| `/_sw-version/` | Deployed `CACHE_VERSION` / `APP_VERSION` vs the live service-worker version ([`docs/offline-map.md`](offline-map.md)). |
| `/_push-demo/` | Web Push smoke test ([`docs/push-notifications.md`](push-notifications.md)). |

## HTMX partial routes

Fragment endpoints return an inner HTML snippet, not a full page, and are
guarded by `require_htmx` (a plain HTTP request gets a 400 — invariant 4 in
[`CLAUDE.md`](../CLAUDE.md)). They live under a `partials/` prefix:

- `/partials/weather/<region_id>/<date_str>/` — weather snippet
  ([`docs/weather-header.md`](weather-header.md), [`docs/async-operations.md`](async-operations.md))
- `/partials/season/<region_id>/` — season calendar ([`docs/calendar.md`](calendar.md))
- `/partials/report/`, `/partials/report/form/` — field-report submission
- `/favourites/partials/…` — create, rename, delete, toggle, card, list
- `/routes/partials/…` — create (multipart GPX upload), rename, delete, list.
  `list` takes `?variant=map` for the map sheet's lean row (SNOW-686); the
  surface that reaches these is open to every visitor and its contents to
  every signed-in one ([`docs/map-and-api.md`](map-and-api.md))
- `/partials/_components/<slug>/` — component-library panels

## Non-HTML routes

`/api/…` JSON endpoints ([`docs/map-and-api.md`](map-and-api.md)),
`/api/mcp/` ([`docs/mcp-server.md`](mcp-server.md)),
`/api/telemetry` ([`docs/telemetry-pipeline.md`](telemetry-pipeline.md)),
`/favourites/favourites.geojson`, plus the PWA and crawler surface served from
`config/urls.py`: `/sw.js`, `/sw-kill.js`, `/manifest.webmanifest`,
`/robots.txt`, `/sitemap.xml` (`BulletinSitemap`), `/llms.txt`,
`/llms-full.txt`, `/favicon.ico`, and the `/livez` + `/healthz` probes
([`docs/deployment.md`](deployment.md)).

## Page metadata

Every template extending `public/base.html` must override
`{% block page_meta %}` exactly once, or opt out explicitly with `sharing=False`
plus a reason. `tests/public/test_page_meta.py` walks every public URL above and
fails a page in neither state — so a new route here needs a metadata decision
before it can merge. See
[`docs/decisions/page-metadata-is-explicit-or-opted-out.md`](decisions/page-metadata-is-explicit-or-opted-out.md).

---

## Historical: the AI-generated summary design (superseded)

The original design for this site rendered an **AI-generated summary** of each
bulletin rather than the bulletin itself: a home route that redirected to a
random region, a single bulletin-viewer route, and a summary payload shaped
like this:

`overallVerdict` (GO / CAUTION / AVOID), `verdictColour`, `dangerLevel`,
`summary`, `outlook`, activity ratings (`onPiste`, `offPiste`, `skiTouring`,
each `{rating, notes}`), `keyHazards[]`, `bestBets[]`, and a structured
`weather` object (`summitTemp`, `midTemp`, `resortTemp`, `freezingLevel`,
`wind`, `visibility`, `newSnow24h`, `baseDepth`).

**None of these fields exist in the codebase.** The pipeline stores normalised
CAAML v6 and renders it through the versioned render model
([`docs/render-model.md`](render-model.md)); where a summary field had a
CAAML-derivable equivalent it was derived, and sections with no available data
are omitted from the template context so the template hides them. The module
docstring of [`apps/public/views.py`](../apps/public/views.py) still points here
to explain that gap — that reference is the reason this section is retained.

The prototype's visual identity (warm earth tones, Playfair Display headings,
a 780px centred column) is likewise retired. The site is built with Tailwind v4
design tokens; the canonical reference is the component library at
`/_components/` and [`docs/design-system.md`](design-system.md). DM Sans and
DM Mono survive from that palette and are still the body and label faces
(`src/css/main.css`).
