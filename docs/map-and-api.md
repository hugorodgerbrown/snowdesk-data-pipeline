---
name: map-and-api
description: / (public:home) MapLibre choropleth, scrubber, favourites/community-reports overlays, /api/ endpoints (ratings, geojson, summary, groupings)
status: current
last-reviewed: 2026-07-25
---

# Map page and JSON API

`/` (`public:home`) renders a MapLibre GL JS choropleth of Swiss avalanche
regions. Tapping a region opens a bottom sheet with today's danger rating,
resort list, and a CTA to the full bulletin. `/map/` permanently redirects
here (301, query string forwarded). The template (`public/templates/public/home.html`)
extends `base.html`. Static assets are `static/js/map.js` and `static/css/map.css`.

The map JS reads endpoint URLs from `data-*` attributes on the `#map` element,
so `{% url %}` in the template remains the single source of truth for all three
API paths.

**Search**: the header hosts a client-side autocomplete over the regions +
resorts data already fetched at load time (no extra round-trips). Matching is
diacritic-insensitive, prefix hits rank above substring hits, and results
carry a "Region" or "Resort" badge to disambiguate cases where a resort
shares its name with its parent region (e.g. "Davos"). Selecting a result
routes through the same `selectFeature` helper used by the map click handler.
The homepage links to `/map/` via an "Explore the map →" CTA next to the
existing sample-bulletin button.

**Basemap layer picker (SNOW-58)**: a Google-Maps-style stacked-layers
pill in the top-right utility cluster opens a popover of basemap radio
options. The catalogue is `settings.BASEMAP_STYLES` × `_BASEMAP_LABELS`
(in `public/views.py`); the view passes `basemaps` (ordered list of
`{key, label, url}`) and `default_basemap_key` (the env-resolved
fallback) to the template. The user's choice is persisted in
`localStorage["snowdesk.map.basemap"]`; on boot, the JS uses the stored
key when it matches a current catalogue entry and otherwise falls back
to `default_basemap_key`. Selecting a new basemap calls
`MAP.setStyle(url)`; a `style.load` handler in the main IIFE
re-installs the `regions` source + `regions-fill` / `regions-line` /
`regions-label` layers and restores the selected-region outline plus
any `?d=`-driven scrubber paint. Layer-bound click / mouseenter /
mouseleave handlers survive the swap because they're bound by layer id.
A `snowdesk:basemap-changing` event is dispatched before the swap so an
active timelapse stops cleanly. The PWA shell service worker
([`offline-map.md`](offline-map.md)) is **not** involved in basemap
swaps — tile fetches go straight to the network so a stale basemap
can never linger in the cache.

The `openfreemap_liberty` catalogue entry reads its style URL from
`settings.OPENFREEMAP_STYLE_URL` (SNOW-242), defaulting to
`https://tiles.openfreemap.org/styles/liberty`. The CSP `connect-src`
origin is derived from the same setting, so switching the basemap origin
(e.g. onto a self-hosted deployment) is an env-var change with no code
deploy. Live self-hosting — CORS, PMTiles range requests, and the
subdomain — is tracked in SNOW-485.

**Season scrubber and timelapse**: a horizontal scrubber sits at the
bottom of the map. The thumb defaults to today's position within the
Nov–May window; dragging recolours every region from the
`/api/ratings/?country=ch` payload to show how danger evolved on the
selected date, and pressing the play button steps through the season as
a timelapse. The drawer (when open) follows the scrubber via the
`snowdesk:date-changed` event, fetching `/api/region/<id>/summary/?d=…`
so the bulletin shown matches the scrubbed-to date. The full-season
payload is fetched lazily on first scrubber interaction and cached for
the session via `getSeasonRatings()` in `static/js/map.js` — first scrub
pays the round-trip; subsequent scrubs and timelapse playback render
from the in-memory cache.

**Favourites overlay (SNOW-414)**: an eligible (authenticated) visitor
sees an "Add favourite" pill in the bottom-right
control stack (`#map-controls-br`) and a `favourites` overlay toggle in the
basemap menu's Overlays section, both rendered by `public/views.py`'s
`_favourites_context()` and `_map_embed.html`. `#map` carries
`data-favourites-eligible="true|false"` always, and `data-favourites-url`
(the per-user `favourites:geojson` endpoint) only when eligible — anonymous
and ineligible visitors' browsers never fetch the per-user endpoint.
Unlike the resorts overlay, favourites defaults **on**
(`overlayState.favourites`, `localStorage["snowdesk.map.overlay.favourites"]`)
and is fetched eagerly at boot for eligible users rather than waiting for a
toggle, since it's the user's own saved data rather than a public dataset.
The `favourites-pin` layer is a `symbol` layer rendering a `★` glyph (no
sprite image needed) plus a zoom-banded `favourites-label` showing each
pin's name; tapping an *existing* pin dispatches `snowdesk:favourite-selected
{uuid, name, container}` — map.js passes an empty `[data-favourite-detail]`
container that `static/js/favourites.js` fills with the rename/delete markup,
then map.js anchors it in a MapLibre popup at the pin (SNOW-499: an existing
favourite is a point fixed to the map, so its detail is a *pinned popup*, not
the docked create/placement sheet — the sheet stays put only while the map
pans a mobile pin under it during placement). A successful rename/delete
round-trip dispatches `snowdesk:favourites-changed` (re-fetch geojson +
`setData`), and delete additionally dispatches `snowdesk:favourite-detail-close`
so map.js removes the popup. The "Add favourite" flow itself (placement,
drag-to-refine, the create sheet, CSRF handling) is documented in
`favourites/views.py`'s module docstring and `static/js/favourites.js`'s
header comment — there is no server-side "fetch one favourite" endpoint, so
the pin-detail rename/delete markup is reconstructed client-side from the
`__UUID__`-templated
`favourite_rename_url_template` / `favourite_delete_url_template` context
vars — the same reverse-with-a-dummy-id-then-string-replace trick
`public/views.py::home()` uses for `edit_save_url_template`
(`api:edit_resort_save_coords`).

**Marker exclusion zone (SNOW-445)**: favourite, community-observation,
and report-cluster markers sit on top of the region choropleth. MapLibre
has no `stopPropagation` between layer-scoped click handlers, so a tap on a
marker used to fire *both* the marker's handler and the region-fill handler,
selecting the region (and popping its tooltip) underneath the marker — a
jarring double-action. All click routing is now consolidated into a single
generic `map.on('click')` dispatcher in `static/js/map.js` (the per-layer
`click` handlers were removed; only their `mouseenter`/`mouseleave`
cursor affordances remain). Priority is **overlay controls → markers →
resort pin → region fill → empty-area deselect**. A tap on a marker glyph
(`markerUnderPoint()`) activates that marker (`activateMarker()`) and never
selects the region. The hotspot is the glyph's own rendered hit-area — an
exact-point `queryRenderedFeatures`, the same hit-test that drives the
desktop pointer cursor via the `mouseenter` handlers — so the tappable area
matches the affordance the user sees and honours each icon's anchor/offset
(no padding box). **Resort pins are deliberately excluded** from this set — a resort
pin is a proxy for its parent region, so tapping one still selects that
region. Because the dispatcher is a plain (non-layer-scoped) listener, it is
also the one handler a synthetic `MAP.fire('click', …)` reaches, which is
how the e2e suite (`tests/e2e/test_map_marker_exclusion.py`) drives it
deterministically.

The same markers are also kept **always on top** of every other layer.
MapLibre paints layers in insertion order and the overlays are
lazy-installed at unpredictable times (a polygon overlay toggled on after
the pins exist, or the whole style re-added after a basemap swap), so a pin
can otherwise end up buried. `raiseMarkerLayers()` (in `static/js/map.js`)
re-lifts the favourite and community-observation layers to the top and is
called at the end of **every** layer-install path (`installRegionsLayers`,
`installOverlayLayers`, `installResortsLayer`, `installFavouritesLayer`,
`installCommunityReportsLayer`, `installBulletinGroupingsLayer`) — so any new
install must keep that call, or a later overlay will paint over the pins.

**Route ordering**: `/map/` (the redirect) is registered before `<str:region_id>/` in
`public/urls.py`. Do not reorder these — Django matches URL patterns
top-to-bottom and the generic region pattern would swallow `/map/` if it
appeared first.

**JSON API** — plain `JsonResponse` views, no DRF. Mounted at `/api/` in
`config/urls.py` under the `api:` namespace (`public/api_urls.py`):

| URL | Name | Response |
|-----|------|----------|
| `GET /api/ratings/` | `api:ratings` | `{date_iso: {region_id: rating_int}}` — unified ratings endpoint (SNOW-239). Accepts optional `?d=YYYY-MM-DD` (restrict to one date) and `?country=ch\|fr\|at\|it` (restrict by country). Cold open uses `?d=<today>&country=ch` (~2 KB); first scrubber interaction uses `?country=ch` (full CH season, ~40 KB). Compact int encoding: `0=no_rating, 1=low, 2=moderate, 3=considerable, 4=high, 5=very_high`. Server-side `cache.get_or_set` keyed on `(country, date)` keeps DB hits to one per cache window (5 min for single-date, 1 h for full-season). |
| `GET /api/resorts-by-region/` | `api:resorts_by_region` | `{region_id: [resort_name, …]}` — alphabetical; regions without resorts omitted |
| `GET /api/resorts.geojson` | `api:resorts_geojson` | GeoJSON FeatureCollection of geocoded resorts (Points; `[lon, lat]` per RFC 7946); properties `id`, `name`, `region_id`, `needs_review` |
| `GET /api/regions.geojson` | `api:regions_geojson` | GeoJSON FeatureCollection from `Region.boundary` (L4 fixture regions); each feature has `properties.id` + `properties.name` |
| `GET /api/major-regions.geojson` | `api:major_regions_geojson` | GeoJSON FeatureCollection of L1 EAWS major regions (e.g. `CH-4`, `CH-5`) with `properties.id` + `properties.name`. |
| `GET /api/sub-regions.geojson` | `api:sub_regions_geojson` | GeoJSON FeatureCollection of L2 EAWS sub-regions (e.g. `CH-41`, `CH-42`) with `properties.id` + `properties.name`. |
| `GET /api/region/<region_id>/summary/` | `api:region_summary` | `{html, level}` — `html` is the server-rendered MapLibre Popup snippet (danger-rating chip + geographic breadcrumb); `level` is the rating string the JS uses to stamp `data-level` on the popup container for the border colour. Honours `?d=YYYY-MM-DD` so the popup can show any scrubbed-to date; returns 400 on a malformed value. |
| `GET /api/bulletin-groupings.geojson` | `api:bulletin_groupings_geojson` | `{"type":"FeatureCollection","features":[…]}` — a **single day's** dissolved bulletin boundaries. `?d=YYYY-MM-DD` is **required** (400 `date_required` if absent, 400 `malformed date` on a bad value). Each feature's geometry is the dissolved outer boundary of all L4 micro-regions sharing that bulletin; `properties` carries `bulletin_id`, `date`, and `countries` (sorted ISO-2 list). Accepts optional `?country=ch\|fr\|at\|it`; filters by membership in the `countries` list (a cross-border bulletin with `["AT","IT"]` appears for both `?country=at` and `?country=it`). Server-side `cache.get_or_set` keyed on `(country, date)` (5 min). **Why single-date:** the endpoint previously returned the whole season keyed by date in one payload; once the historical backfill landed, serialising every day's dissolved geometry at once pushed the web worker past its 512 MB limit (SNOW-323 follow-up). The JS overlay ("Bulletin groupings", `data-overlay-key="l3"`) now fetches one day at a time via `fetchBulletinGroupingsForDate(dateKey)` (no `?country=` filter, so cross-border rows are present), memoising each date for the session. It draws the boundary only once the scrubber **settles** (`GROUPINGS_SETTLE_MS = 250`), blanking the layer during active drag/playback so it neither thrashes the network nor lags a frame behind the choropleth. The MapLibre layer uses an array-membership filter (`['in', c, ['get','countries']]`) instead of the scalar `match` filter used by L1/L2, because `countries` is a JSON list not a string. |
| `GET /api/community-reports.geojson` | `api:community_reports_geojson` | `{"type":"FeatureCollection","features":[…]}` — anonymised, clustered "Community reports" overlay (SNOW-419). Covers `FieldObservation` rows from the last 48 hours. Each feature's `coordinates` are `[lon, lat]` rounded to 3 dp (~80–110 m); `properties` carries `type` (`OBSERVATION_TYPE` value), `type_label` (display label), `observed_at` (ISO, floored to the nearest 15 min), and `region_name` (or `null`). Never serialises `latitude`/`longitude` at full precision, `gps_*`, `accuracy_radius_km`, `user`, or the row's pk. `Cache-Control: private, no-store` — unlike the other geojson endpoints it is **not** publicly cacheable and **not** in `_POSTHOG_EXEMPT_PATHS` (SNOW-459); public caching is tracked separately (SNOW-469). It carries a 120s client-side freshness window via `X-Data-Max-Age`. The JS overlay (`data-overlay-key="community_reports"`, default **off**) clusters the source client-side (`cluster: true`) and fades pins by age via a client-computed `_ageOpacity` feature property (no MapLibre "now" expression exists). |

The data flow on map load is: `map.js` fetches `?d=<today>&country=ch` and
`regions.geojson?country=ch` in parallel. Once both resolve, it calls
`map.setFeatureState({source: 'regions', id: numericFeatureId}, {rating})` for
each region, so the choropleth fill layer reads exclusively from feature-state
(no property-based fallback). Regions with no bulletin today are absent from the
ratings response; their feature-state is left unset and the `match` expression
defaults to `no_rating`.

The shared top-nav partial used on the map and other public pages is
documented separately in [`nav_implementation_spec.md`](nav_implementation_spec.md).

## Edit-resorts mode (SNOW-74) — gated on the `edit_map` waffle flag

`/?edit=resorts` enters resort-coordinate-edit mode when the
`edit_map` waffle flag is active for the request user (SNOW-86; see
[`feature-flags.md`](feature-flags.md)). The page renders a right-hand panel with a
queue of resorts that need geocoding (`Resort.objects.needs_geocoding()`
— missing coords or `needs_review=True`) plus a search box across all
resorts. Clicking the map drops a draggable orange pin; drag to refine,
then **Save**. Behind the scenes the panel POSTs `{latitude, longitude}`
to the save endpoint, which sets `geocode_source="manual"`,
`geocode_confidence=1.0`, `geocoded_at=now()`, clears `needs_review`,
and returns the next queue entry so the panel auto-advances. Click an
existing resort point to re-position it.

| URL | Name | Method | Notes |
|-----|------|--------|-------|
| `/api/edit/resorts/queue/` | `api:edit_resorts_queue` | GET | Flag-gated. Returns `{queue, all_resorts}` — queue ordered `region_id ASC, name ASC` so the panel can group rows by L1 area (e.g. `CH-4`). `needs_review` rows still surface a ⚠ in the panel; they are no longer a sort key. |
| `/api/edit/resorts/<int:resort_id>/coords/` | `api:edit_resort_save_coords` | POST | Flag-gated. JSON body `{latitude, longitude}`; coordinates outside `_SWISS_BBOX` are hard-rejected with 400. |

Both endpoints 404 when the `edit_map` flag is inactive
(`_require_edit_map_flag()` in `public/api.py` raises `Http404`). The
page itself silently falls back to the normal map when `?edit=resorts`
is set without the flag (`public/views.py`), so the URL is safe to
bookmark.

Coordinate-ordering pitfall (called out in `static/js/map_edit_resorts.js`):

- DB columns: `latitude`, `longitude`.
- JSON wire format: `{"latitude": …, "longitude": …}` (keyed by name — unambiguous).
- GeoJSON: `coordinates: [longitude, latitude]` per RFC 7946.
- MapLibre marker: `marker.getLngLat()` returns `{lng, lat}` (note `lng`).

### Persisting edits — `dump_resorts_fixture`

Edits land in the local SQLite, not the source-of-truth fixture in git.
Run the dump command after a session of placements to regenerate
`regions/fixtures/resorts.json`:

```bash
uv run python manage.py dump_resorts_fixture          # dry-run, prints diff
uv run python manage.py dump_resorts_fixture --commit # writes the file
git diff regions/fixtures/resorts.json                    # review
```

The dump uses `use_natural_foreign_keys=True` (so `region` round-trips
as `["CH-4115"]` not a numeric pk), pretty-prints with `indent=2`, and
orders by pk — the same shape as the existing fixture. Without the
dump step, edits live only on the operator's laptop and silently
disappear on `loaddata` re-runs. Mirrors `refresh_eaws_fixtures`'s
safe-by-default convention (read-only without `--commit`).
