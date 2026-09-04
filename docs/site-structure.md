---
name: site-structure
description: Public route map — two documents (bulletin, /weather/<short_id>/) and the map; /resorts/<slug>/; SNOW-795 redirects; HTMX partial prefixes
status: current
last-reviewed: 2026-09-04
---

# Snowdesk site structure

The public URL surface, as routed. Route tables live in
[`config/urls.py`](../config/urls.py) (project level) and
[`apps/public/urls.py`](../apps/public/urls.py),
[`apps/accounts/urls.py`](../apps/accounts/urls.py),
[`apps/favourites/urls.py`](../apps/favourites/urls.py) and
[`apps/observations/urls.py`](../apps/observations/urls.py) — this page is the
map of them, not a second source of truth.

**Two documents and a map** (SNOW-795,
[`docs/decisions/two-documents-and-a-map.md`](decisions/two-documents-and-a-map.md)).
Snowdesk has two document pages — the bulletin at
`/<region_id>/<slug>/<date>/` and the weather page at `/weather/<short_id>/`
— and one application, the map at `/`. Everything else a visitor might
read at length lives on the map as a layer, a pin or a sheet, and the
routes that used to render those things as pages are permanent redirects
into the map (`?panel=favourites|routes|reports` opens a sheet,
`?resort=<slug>` flies to a resort). `/resorts/<slug>/` survives as a
search landing page that routes to the two documents, and
`/account/settings/` because account mechanics are not map objects.
Identifiers follow [`no-integer-pks-in-urls`](decisions/no-integer-pks-in-urls.md):
no sequential primary key appears in a URL or a public feed.

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
| `/weather/<short_id>/` | `location_weather` | **Document two**: one location, one day (SNOW-761/789). `?date=` picks the row inside the page; the bare URL is canonical for today, the dated one for a past day (SNOW-799). Keyed on `Location.short_id` — eleven opaque characters, never the pk (SNOW-797); `/weather/<int>/` 301s. |
| `/resorts/<slug>/` | `resort_detail` | A router, not a document (SNOW-807): the resort's own curated facts, then one link to the region's bulletin, a link per curated location to its weather page, and one link to the map with the reports sheet open at the resort. Keyed on `Resort.slug` (SNOW-796); `/resorts/<id>/<slug>/` 301s. |
| `/favourites/<uuid>/` | redirect | Permanent 301 to the pin's weather page (SNOW-800); 404 for a pin with no location. |
| `/observations/` | redirect | Permanent 301 to `/?panel=reports` — the map with the reports sheet open (SNOW-804). |
| `/trips/new/` | `trips.views.trip_new` | The authoring form for a trip planned from one of your own routes, named by `?route=<uuid>` (SNOW-820). A page rather than a fragment in the map's routes panel, whose body is re-cloned on every open. Anonymous visitors redirect to sign-in. |
| `/trips/s/<token>/` | `trips.views.trip_share_page` | **The page behind a trip's share link** (SNOW-821). Public, rate-limited on the (token, IP) key, `Cache-Control: no-store`. Renders the same summary and map the object page does; unknown, revoked and expired tokens are one 404. Emits the full sharing set AND `noindex` — unfurling as a card is the point of the link, being findable in search would defeat the token, and `Disallow: /trips/` in robots.txt would block the unfurlers themselves. |
| `/trips/<uuid>/` | `trips.views.trip_detail` | **One trip's own page** — the day, the meeting time and point, the route drawn with a marker, the figures, the elevation profile and the organiser's note (SNOW-820). Organiser-only; SNOW-821 adds the tokenised public page and SNOW-822 opens this one to the roster. A page, not a redirect into the map, because a trip is authored to be sent ([why](decisions/two-documents-and-a-map.md)). |
| `/how-to-read-a-bulletin/` | `how_to_read_bulletin` | Domain primer for readers. |
| `/help/` | `help_page` | Help, including the PWA/offline sections. |
| `/examples/random/` | `examples_random` | A sample bulletin; `/random/` is a deprecated redirect to it. |
| `/examples/category/<danger_level>/` | `examples_category` | A sample bulletin at a given danger level. |
| `/s/<token>/` | `share_redirect` | Short share links. |
| `/privacy/`, `/terms/`, `/terms-of-service/`, `/colophon/` | static pages | Legal and attribution. |

Account routes (`/account/…` — settings, register, verify, setup, sign-in,
change-email, unsubscribe, WebAuthn, push) are documented in
[`docs/accounts.md`](accounts.md). `/account/`, `/account/favourites/`,
`/account/observations/` and `/account/routes/` are permanent redirects into
the map's sheets (SNOW-802/803); `/account/settings/` is the one account
page.

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

- `/partials/season/<region_id>/` — season calendar ([`docs/calendar.md`](calendar.md))
- `/partials/report/`, `/partials/report/form/` — field-report submission
- `/favourites/partials/…` — create, rename, delete, resort toggle, region
  pin toggle (SNOW-802), card, list
- `/routes/partials/…` — create (multipart GPX upload), rename, delete, list.
  Every list endpoint has one shape, the map sheet's (SNOW-803 retired the
  account-page variants); the surface that reaches these is open to every
  visitor and its contents to every signed-in one
  ([`docs/map-and-api.md`](map-and-api.md))
- `/trips/partials/…` — create, edit, delete (SNOW-819). The trip PAGES
  sit outside this prefix because they are navigations, and so do
  `/trips/<uuid>/share/` and `.../share/revoke/`, which answer JSON to a
  plain `fetch()` for the native share sheet; only the writes above are
  fragments, so an invalid submission can come back as the form with its
  errors
- `/partials/_components/<slug>/` — component-library panels

## Non-HTML routes

`/api/…` JSON endpoints ([`docs/map-and-api.md`](map-and-api.md)),
`/api/mcp/` ([`docs/mcp-server.md`](mcp-server.md)),
`/api/telemetry` ([`docs/telemetry-pipeline.md`](telemetry-pipeline.md)),
`/favourites/favourites.geojson`, plus the PWA and crawler surface served from
`config/urls.py`: `/sw.js`, `/sw-kill.js`, `/manifest.webmanifest`,
`/robots.txt`, `/sitemap.xml`, `/llms.txt`,
`/llms-full.txt`, `/favicon.ico`, and the `/livez` + `/healthz` probes
([`docs/deployment.md`](deployment.md)).

`/sitemap.xml` has four sections, defined in
[`apps/public/sitemaps.py`](../apps/public/sitemaps.py) and registered together
as `SITEMAPS`: `bulletins` (regions with a bulletin valid today), `resorts`
(every resort page), `locations` (every *named* public location's weather
page — never a centroid, never a favourite's pin; SNOW-799) and `static`
(the homepage, both guides, the four legal pages). SNOW-676 added `resorts`
and `static` — until then the sitemap was the bulletin section alone, which
left the resort pages invisible to search and made the whole file *empty*
out of season, when no bulletin is valid today. Deliberately absent, with
the reasoning in the module: `/examples/…` (serves a random bulletin per
request), redirects (`/observations/` is one now), staff-only routes, and
anything `Disallow`ed in robots.txt.

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
