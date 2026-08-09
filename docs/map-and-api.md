---
name: map-and-api
description: / (public:home) MapLibre choropleth, scrubber, overlays, /api/ endpoints (ratings, geojson, summary, groupings, forecast-weather)
status: current
last-reviewed: 2026-08-07
---

# Map page and JSON API

`/` (`public:home`) renders a MapLibre GL JS choropleth of Swiss avalanche
regions. Tapping a region selects it — outline, season ribbon, readout chip
(breadcrumb + danger swatch + bulletin roundel) and the `#CH-4115` URL
fragment; tapping it again, or tapping empty map area, deselects. Selecting
a region deliberately opens **no** popup: the region detail popup
(`openRegionPopup`, fed by `api:region_summary`) is still implemented in
`map.js` but has no trigger, because it covered the terrain the visitor had
just tapped. `/map/` permanently redirects
here (301, query string forwarded). The template (`apps/public/templates/public/home.html`)
extends `base.html`. Static assets are the `static/js/map*.js` set (see
"Module layout" below) and `static/css/map.css`.

### Module layout (SNOW-610)

`map.js` was one 9,192-line file until SNOW-610 split it along the eleven
IIFE seams it already contained. It is now the boot IIFE alone — style and
overlay install, region select, popups, markers, search — and the surfaces
are siblings. `map.js`'s own header carries the full list with a line on
what each file owns; `apps/public/templates/public/home.html` carries the
script tags.

**The order of those tags is load-bearing.** These are classic scripts, so
they share one global lexical scope: a top-level `let`/`const` in one file
is readable from a later one as a bare identifier, but sits in the temporal
dead zone until its own script has run. Every IIFE runs at parse time. So
the three declaration files — `map_state.js`, `map_basemap_downloads.js`,
`map_shared.js` — load before `map.js`, and the nine surface files after
it, in the order they held inside the single file.

Modules *outside* this set (`map_layer_sync_status.js`, `favourites.js`)
must reach the shared state through `window.snowdeskMapState` instead. The
distinction is not stylistic: a top-level `let` lands in the global lexical
scope but **not** on `window`, which is how `map_layer_sync_status.js` read
`window.MAP` for its entire life and always got `undefined`. See
`map_state.js`'s header.

`tests/js/_load_map_bundle.js` models the same order for Vitest, which
would otherwise evaluate each file as an ES module and module-scope every
one of those shared bindings.

**The order is enforced, not hand-maintained** (SNOW-647).
`tests/public/test_map_script_order.py` renders the homepage and asserts its
script sequence matches `MAP_BUNDLE`, that the bundle loads as one
uninterrupted run, and that declarations precede `map.js` while surfaces
follow it. Adding a map module therefore means editing two places — the
template and `MAP_BUNDLE` — and forgetting either is a failing test rather
than a map that stops booting. `map.js`'s header list is prose and is
deliberately not asserted.

The map JS reads endpoint URLs from `data-*` attributes on the `#map` element,
so `{% url %}` in the template remains the single source of truth for all three
API paths.

**Search**: the header hosts a client-side autocomplete over the regions +
resorts data already fetched at load time (no extra round-trips). Matching is
diacritic-insensitive, prefix hits rank above substring hits, and results
carry a "Region" or "Resort" badge to disambiguate cases where a resort
shares its name with its parent region (e.g. "Davos"). Selecting a result
routes through the same `selectFeature` helper used by the map click handler.
The homepage *is* the map, so there is no "go to the map" link: the
"Explore the map" button inside the `#home-intro` overlay
(`public/partials/_map_embed.html`) is a **dismiss** control — it clears the
landing overlay to reveal the map already mounted behind it, and additionally
opens the map-help coachmark tour (SNOW-535), which the overlay's "×" does
not. The overlay's only outbound link is "Register"; `/examples/random/` is
reachable by URL but is not linked from here.

**Basemap layer picker (SNOW-58)**: a Google-Maps-style stacked-layers
pill in the top-right utility cluster opens a popover of basemap radio
options. The catalogue is `settings.BASEMAP_STYLES` × `_BASEMAP_LABELS`
(in `apps/public/views.py`); the view passes `basemaps` (ordered list of
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
the session via `getSeasonRatings()` in `static/js/map_shared.js` — first scrub
pays the round-trip; subsequent scrubs and timelapse playback render
from the in-memory cache.

**Favourites overlay (SNOW-414)**: an eligible (authenticated) visitor
sees an "Add favourite" pill in the bottom-right
control stack (`#map-controls-br`) and a `favourites` overlay toggle in the
basemap menu's Overlays section, both rendered by `apps/public/views.py`'s
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
`apps/favourites/views.py`'s module docstring and `static/js/favourites.js`'s
header comment — there is no server-side "fetch one favourite" endpoint, so
the pin-detail rename/delete markup is reconstructed client-side from the
`__UUID__`-templated
`favourite_rename_url_template` / `favourite_delete_url_template` context
vars — the same reverse-with-a-dummy-id-then-string-replace trick
`apps/public/views.py::home()` uses for `edit_save_url_template`
(`api:edit_resort_save`).

**Weather overlay (SNOW-573)**: a Meteocons condition symbol plus the
day's max temperature at each resort-anchored (and, for a signed-in
visitor, favourite-anchored) `ForecastPoint`. Flag-gated on `weather_layer`
(`superusers=True` — see `docs/feature-flags.md`); `#map` carries
`data-weather-layer-eligible="true|false"` and always emits
`data-forecast-weather-url` (the endpoint itself 404s while the flag is
inactive, mirroring the always-emitted `data-community-reports-url`
rather than the eligible-gated `data-favourites-url`). Default **off**,
like community reports. The public payload (`/api/forecast-weather.geojson`,
below) carries resort-anchored points only — a `Favourite`-only
`ForecastPoint` never appears there; a signed-in visitor's own pins are
merged into the same `weather` MapLibre source client-side from
`favourites.geojson`'s `days` property (also flag-gated), which
`apps.weather.services.weather_display.build_point_weather_days` builds
identically for both endpoints so the two can't drift.

Point weather is forecast-only (`POINT_FORECAST_DAYS = 7` days from today,
often fewer — ICON-CH2 commonly returns 5) and every date's data ships in
one payload, so the client never re-fetches on a scrubbed date: it
re-projects the already-fetched FeatureCollection in memory
(`window.pwaWeatherCore.projectFeatureCollectionForDate`, `static/js/
map_weather_core.js`) and calls `source.setData()`. A scrubbed date outside
the payload's stored window (`isDateInForecastWindow`) disables the
layers-menu row with a reason, via a namespaced marker
(`data-weather-disabled-out-of-window`) distinct from
`map_layer_sync_status.js`'s own offline-gating marker, so the two
disable reasons never clobber each other's re-enable.

The Meteocons icons are the one layer on this map that can't use the
single-colour SDF `Path2D`-fill technique the favourite star / community-
report flag use (`sdf: true` discards colour, and Meteocons are multi-path
and gradient-filled) — they're rasterised full-colour via an `<img>`
decode into a module-level cache keyed by filename
(`decodeWeatherIcon`/`ensureWeatherIconsRegistered` in `map.js`), decoded
once per filename and re-registered synchronously from that cache after a
basemap `setStyle` wipes `map.addImage`'s registrations, preserving the
same "image id exists before `addLayer` references it" invariant every
other icon on this map relies on.

Offline caching: `OVERLAY_RESOURCES.weather` in `map_layer_sync_status.js`
is `kind: 'idb'` (not `'geojson'`) — the payload is a mutable forecast, not
static reference data suited to `sw.js`'s never-expiring `STATIC_PATHS`
shell cache. It write-through-caches to IndexedDB
(`window.pwaMapOverlayCache`, same posture as `community_reports`) and
self-corrects on read-back: a stale cached payload simply stops drawing
anything as scrubbed dates roll past its forecast window.

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
re-lifts the favourite, community-observation, and weather layers to the
top and is called at the end of **every** layer-install path
(`installRegionsLayers`, `installOverlayLayers`, `installResortsLayer`,
`installFavouritesLayer`, `installCommunityReportsLayer`,
`installBulletinGroupingsLayer`, `installWeatherLayer`) — so any new
install must keep that call, or a later overlay will paint over the pins.

**Placement focus (`static/js/map_placement_focus.js`)**: while a pin is
being positioned — a favourite, a field observation, or a resort in the
Edit-resorts tool — every layer the app draws over the basemap is hidden,
then restored to exactly its previous visibility when the flow ends.
`window.PlacementFocus` exposes `enter()` / `exit()` / `isActive()`;
`enter()` snapshots each layer's `visibility` before setting it to `none`,
so an overlay the user had switched off (resorts, say) is still off
afterwards. Re-entry while already focused is a no-op, so the snapshot
can't be clobbered by a double-arm.

The set of layers to hide is **derived, not enumerated**: every Snowdesk
layer hangs off a `geojson` source this app adds, and every basemap layer
hangs off a `vector`/`raster` source (or is a sourceless `background`
layer, as in the offline fallback style), so the module hides exactly the
geojson-sourced layers. A new overlay is covered without touching the
module — unlike the `OVERLAY_LAYER_IDS` lists (`map_basemap_picker.js`,
and `OVERLAY_LAYER_IDS_MAIN` in `map.js`), which must
enumerate ids because they map *menu rows* to layers.

`regions-fill` appears in neither of those lists (SNOW-656). It is the only
overlay layer hidden by **opacity** rather than visibility, because it is the
map's hit-test target and `queryRenderedFeatures` returns nothing from a
layer at `visibility: none`; its layout is derived from both the Micro
regions and Bulletins rows by
`window.pwaLayerVisibilityCore.regionsFillLayout` and applied through
`applyBulletinsVisibility` in `map.js`, which is the single writer. Placement
focus is unaffected — it snapshots and restores whatever `visibility` it
finds.

Callers: `place_picker.js`'s `activate()`/`deactivate()` (which covers the
favourite-create and field-observation flows, both of which position via
the shared placement pin), and `map_edit_resorts.js`'s
`placeDraftMarker()`/`removeDraftMarker()` — the draft marker's lifetime is
exactly the positioning phase, since Save and Cancel are enabled only while
it exists. `enter()`/`exit()` dispatch `snowdesk:placement-focus
{active}`; map.js listens for it and closes any open region/detail popup on
entry (a card anchored to a region that is no longer drawn is a leftover).
Popups are not reopened on exit. A basemap swap mid-placement re-installs
every overlay visible, so the module re-hides and re-snapshots on
`snowdesk:basemap-changed`. Tests: `tests/js/test_map_placement_focus.js`
(module bookkeeping against a fake map) and
`tests/e2e/test_map_placement_focus.py` (the real wiring).

**Placement pin lift (`static/js/place_picker.js`, SNOW-538)**: the pin the
user pans the map under is *not* pinned to the map's centre. Both placement
sheets dock to the bottom of the viewport and size themselves to their
content, and on a phone the report form (a location line, five problem
buttons, Cancel) is tall enough to reach past the map's vertical midpoint —
a centred pin sat behind it, so the user could not see the point they were
placing. Callers pass their sheet as `activate({occludedBy})`; the picker
measures it against the map container and, when the sheet covers where the
pin would otherwise sit, lifts the pin to the middle of the strip of map
still visible above it (`--map-place-pin-lift`, consumed by
`.map-place-pin`'s `top` in `static/css/map.css`).

Two consequences follow. The chosen coordinate is **unprojected from the
pin's own screen point**, never `MAP.getCenter()` — the two are the same
only while the lift is zero, which is the wide-viewport case where the
sheet is a bottom-right card that never reaches the middle of the map.
And `recenterTo` moves its coordinate under the *pin* (`easeTo` with a
matching `offset`), not under the centre, so report.js's GPS-refine flow
doesn't shift the fix the moment it opens. A `ResizeObserver` on the sheet
plus a window `resize` listener re-measure on layout changes, holding the
already-chosen point under the pin across the move so a sheet growing
under the user's finger never silently re-picks. Tests:
`tests/js/test_place_picker.js` (the geometry, against a fake map) and
`tests/e2e/test_place_pin_clearance.py` (the real thing, at 375x812).

**Route ordering**: `/map/` (the redirect) is registered before `<str:region_id>/` in
`apps/public/urls.py`. Do not reorder these — Django matches URL patterns
top-to-bottom and the generic region pattern would swallow `/map/` if it
appeared first.

**JSON API** — plain `JsonResponse` views, no DRF. Mounted at `/api/` in
`config/urls.py` under the `api:` namespace (`apps/public/api_urls.py`):

| URL | Name | Response |
|-----|------|----------|
| `GET /api/ratings/` | `api:ratings` | `{date_iso: {region_id: rating_int}}` — unified ratings endpoint (SNOW-239). Accepts optional `?d=YYYY-MM-DD` (restrict to one date) and `?country=ch\|fr\|at\|it` (restrict by country). Cold open uses `?d=<today>&country=ch` (~2 KB); first scrubber interaction uses `?country=ch` (full CH season, ~40 KB). Compact int encoding: `0=no_rating, 1=low, 2=moderate, 3=considerable, 4=high, 5=very_high`. Server-side `cache.get_or_set` keyed on `(country, date)` keeps DB hits to one per cache window (5 min for single-date, 1 h for full-season). |
| `GET /api/resorts-by-region/` | `api:resorts_by_region` | `{region_id: [resort_name, …]}` — alphabetical; regions without resorts omitted |
| `GET /api/resorts.geojson` | `api:resorts_geojson` | GeoJSON FeatureCollection of geocoded resorts (Points; `[lon, lat]` per RFC 7946); properties `id`, `name`, `region_id`, `needs_review`, `tier` (SNOW-543 — `CORE`/`STANDARD`/`MINOR`, the curated map-prominence verdict the pin radius is interpolated from) |
| `GET /api/regions.geojson` | `api:regions_geojson` | GeoJSON FeatureCollection from `Region.boundary` (L4 fixture regions); each feature has `properties.id` + `properties.name`, plus `properties.download` (SNOW-521 — see below) when the region has a precomputed offline-basemap size. |
| `GET /api/major-regions.geojson` | `api:major_regions_geojson` | GeoJSON FeatureCollection of L1 EAWS major regions (e.g. `CH-4`, `CH-5`) with `properties.id` + `properties.name`. Never carries `properties.download` — offline-basemap download is a MicroRegion-only feature (SNOW-521). |
| `GET /api/sub-regions.geojson` | `api:sub_regions_geojson` | GeoJSON FeatureCollection of L2 EAWS sub-regions (e.g. `CH-41`, `CH-42`) with `properties.id` + `properties.name`. Never carries `properties.download`, same as major-regions.geojson above. |
| `GET /api/region-basemap-tiles/?id=<region_id>` | `api:region_basemap_tiles` | The full precomputed `basemap_download` blob (incl. `z` tile-index ranges) for one MicroRegion (`MicroRegion.region_id` only — SNOW-521). 400 on a missing `?id=`; 404 for an unknown id or a region with no computed blob yet. Fetched on demand — see [`offline-map.md`](offline-map.md#offline-overlay-caches--download-basemap-snow-492-snow-521) for the full download flow. |
| `GET /api/region/<region_id>/summary/` | `api:region_summary` | `{html, level}` — `html` is the server-rendered MapLibre Popup snippet (danger-rating chip + geographic breadcrumb); `level` is the rating string the JS uses to stamp `data-level` on the popup container for the border colour. Honours `?d=YYYY-MM-DD` so the popup can show any scrubbed-to date; returns 400 on a malformed value. **Currently unused by the client** — the region popup lost its trigger (see the intro above); the endpoint is kept alongside `openRegionPopup` pending a decision on a replacement detail surface. |
| `GET /api/bulletin-groupings.geojson` | `api:bulletin_groupings_geojson` | `{"type":"FeatureCollection","features":[…]}` — a **single day's** dissolved bulletin boundaries. `?d=YYYY-MM-DD` is **required** (400 `date_required` if absent, 400 `malformed date` on a bad value). Each feature's geometry is the dissolved outer boundary of all L4 micro-regions sharing that bulletin; `properties` carries `bulletin_id`, `date`, and `countries` (sorted ISO-2 list). Accepts optional `?country=ch\|fr\|at\|it`; filters by membership in the `countries` list (a cross-border bulletin with `["AT","IT"]` appears for both `?country=at` and `?country=it`). Server-side `cache.get_or_set` keyed on `(country, date)` (5 min). **`Cache-Control` is date-aware (SNOW-526):** `apps.bulletins.services.settled.earliest_mutable_date()` derives a settled/unsettled threshold from the fetcher registry (`apps.bulletins.services.slf_fetcher.get_sources()`), memoised at the call site (`apps.public.api._cached_earliest_mutable_date()`, 60s) to avoid a per-`BulletinSource` DB query on every request; a settled `?d=` gets `public, max-age=604800, immutable`, otherwise `public, max-age=300` (unchanged). The `immutable` token is also `sw.js`'s signal to persist the response for offline use — see `docs/decisions/date-aware-cache-policy.md`. **Why single-date:** the endpoint previously returned the whole season keyed by date in one payload; once the historical backfill landed, serialising every day's dissolved geometry at once pushed the web worker past its 512 MB limit (SNOW-323 follow-up). The JS overlay ("Bulletin groupings" — the boundary now draws alongside the choropleth, SNOW-506; SNOW-521 removed the standalone `data-overlay-key="l3"` layers-menu row, and SNOW-656 moved which row governs it from `l4` to `bulletins`, since the boundary is a bulletin concept and, like the choropleth, is date-bound) fetches one day at a time via `fetchBulletinGroupingsForDate(dateKey)` (no `?country=` filter, so cross-border rows are present), memoising each date for the session. It draws the boundary only once the scrubber **settles** (`GROUPINGS_SETTLE_MS = 250`), blanking the layer during active drag/playback so it neither thrashes the network nor lags a frame behind the choropleth. The MapLibre layer uses an array-membership filter (`['in', c, ['get','countries']]`) instead of the scalar `match` filter used by L1/L2, because `countries` is a JSON list not a string. |
| `GET /api/community-reports.geojson` | `api:community_reports_geojson` | `{"type":"FeatureCollection","features":[…]}` — anonymised, clustered "Community reports" overlay (SNOW-419). Covers `FieldObservation` rows from the last 48 hours. Each feature's `coordinates` are `[lon, lat]` rounded to 3 dp (~80–110 m); `properties` carries `type` (`OBSERVATION_TYPE` value), `type_label` (display label), `observed_at` (ISO, floored to the nearest 15 min), and `region_name` (or `null`). Never serialises `latitude`/`longitude` at full precision, `gps_*`, `accuracy_radius_km`, `user`, or the row's pk. `Cache-Control: private, no-store` — unlike the other geojson endpoints it is **not** publicly cacheable and **not** in `_POSTHOG_EXEMPT_PATHS` (SNOW-459); public caching is tracked separately (SNOW-469). It carries a 120s client-side freshness window via `X-Data-Max-Age`. The JS overlay (`data-overlay-key="community_reports"`, default **off**) clusters the source client-side (`cluster: true`) and fades pins by age via a client-computed `_ageOpacity` feature property (no MapLibre "now" expression exists). |
| `GET /api/forecast-weather.geojson` | `api:forecast_weather_geojson` | `{"type":"FeatureCollection","features":[…]}` — SNOW-573 map Weather overlay: one Point feature per geocoded `Resort` with a linked `ForecastPoint` (never a `Favourite`-only point — see the privacy note above). Geometry is the *resort's* coordinates, not the quantised point's. `properties` carries `resort_id`, `name`, `region_id`, and `days` — a dict keyed by ISO date (`{"2026-08-07": {"icon": "light_snow-day.svg", "label": "Light snow", "tmax": 4.0, "tmin": -3.0, "snow": 2.0}}`), defaulting to the **whole stored forecast window** (mirrors `/api/ratings/`'s date-keyed shape); `?d=YYYY-MM-DD` narrows to one date, 400 `malformed date` on a bad value. Flag-gated on `weather_layer` — 404 while inactive. Server-side `cache.get_or_set` keyed `forecast-weather:v1:<d\|all>` (5 min). `generated_at` is the OLDEST `fetched_at` across the payload's `ForecastPointWeather` rows (a record mixing several points' data is only as fresh as its stalest one); `X-Data-Unsafe-After` is never set (non-safety data). Two queries regardless of resort count: `Resort.objects.resorts().geocoded()` with `select_related("forecast_point")`, then one bulk `ForecastPointWeather.objects.filter(forecast_point_id__in=…)` grouped in Python. |

**Per-region offline-basemap sizing (SNOW-521)**: `properties.download` on
`regions.geojson` (L4 only — MajorRegion/SubRegion never carry it) is a
small, flat summary projected from the region's precomputed
`basemap_download` field (`apps/regions/services/basemap_tiles.py`,
`blob_summary`, populated by `manage.py compute_basemap_download`) —
never computed at request time, since region geometry and the basemap
tile grid are both static:

```json
"download": {"count": 329, "mb": 17, "over_ceiling": false, "centre_tile": {"z": 14, "x": 8520, "y": 5820}}
```

The whole `download` key is omitted when the region has no computed
blob yet. The full blob (incl. `z`, the client-expanded tile ranges) is
only fetched — via `region-basemap-tiles` above — when the user clicks
the region's download icon. **`z`'s shape changed in SNOW-583**: a
region's tiles are now clipped to its real boundary plus a margin tile
(`apps.regions.services.basemap_tiles.build_region_blob`) rather than the
whole bbox rectangle, so `z` is
`{"<zoom>": {"<y>": [xmin, xmax]}}` — one row span per surviving row —
not the rectangular `{"<zoom>": [xmin, xmax, ymin, ymax]}` shape a
custom-area download's locally-built blob still uses (a user-drawn
rectangle genuinely is one; see
[`docs/decisions/region-downloads-clip-custom-areas-dont.md`](decisions/region-downloads-clip-custom-areas-dont.md)).
`static/js/basemap_download_core.js`'s `zoomRows` is the one accessor
every consumer handles both shapes through — required, not optional: this
endpoint is `Cache-Control: public, max-age=86400` with no ETag, so a
returning client can still be served an old rectangle blob for up to 24h
after a deploy. See [`offline-map.md`](offline-map.md) for the full
client-side download flow.

The data flow on map load is: `map.js` fetches `?d=<today>&country=ch` and
`regions.geojson?country=ch` in parallel. Once both resolve, it calls
`map.setFeatureState({source: 'regions', id: numericFeatureId}, {rating})` for
each region, so the choropleth fill layer reads exclusively from feature-state
(no property-based fallback). Regions with no bulletin today are absent from the
ratings response; their feature-state is left unset and the `match` expression
defaults to `no_rating`.

Because rating is feature-state, and `MAP.setStyle()` (the basemap picker)
wipes feature-state along with the source, **every basemap swap has to
repaint the whole choropleth** — otherwise every region falls through to
`no_rating` and the map goes grey. `map.js`'s `repaintAfterStyleSwap` does
that, taking the date from `currentDisplayedDate` in preference to `?d=`:
`?d=` is absent on the ordinary visit, because the scrubber strips it for
today and never writes one for the out-of-season snap to the season's last
populated day.

The fill itself is painted **opaque**, with the translucency baked into the
colours by `compositeOverBackdrop()` in `static/js/choropleth_core.js` — a
translucent fill blends with the basemap, which made one rating render as a
different colour on each of the five basemap styles. See
[`decisions/choropleth-blended-not-translucent.md`](decisions/choropleth-blended-not-translucent.md).

The shared top-nav partial used on the map and other public pages is
documented separately in [`nav_implementation_spec.md`](nav_implementation_spec.md).

## Edit-resorts mode (SNOW-74) — gated on the `edit_map` waffle flag

`/?edit=resorts` enters resort-edit mode when the
`edit_map` waffle flag is active for the request user (SNOW-86; see
[`feature-flags.md`](feature-flags.md)). The page renders a right-hand panel with a
queue of resorts that need geocoding (`Resort.objects.needs_geocoding()`
— missing coords or `needs_review=True`) plus a search box across all
resorts.

**Placement.** Click the map to drop a draggable orange
`maplibregl.Marker`, then drag to refine; selecting a resort that already
has coordinates pre-places the marker so it can be dragged without a first
click. This deliberately does **not** use the shared centre pin
(`window.PlacePicker`, `static/js/place_picker.js`) that the
favourite-create and field-observation flows position with. That surface
exists because a dragged pin is occluded by the finger placing it — a
touch problem. Edit-resorts is a staff tool driven with a mouse on a
desktop, where the trade runs the other way: a marker is anchored to its
coordinate, so zooming in to check a placement keeps the pin locked to the
spot it marks instead of leaving it on screen while the ground moves
underneath. `tests/e2e/test_edit_resorts_panel.py` pins this down, so a
later "unify the placement surfaces" pass has to argue with it rather than
silently regress the tool.

While a draft marker exists the map is cleared to the basemap
(`window.PlacementFocus`), which hides the resort-points layer — so
tapping a pin to jump to that resort only works before a draft is placed;
otherwise pick the row from the panel list. **Cancel** (or Escape)
discards the draft and brings the overlays back.

**Details.** The panel's collapsible "Resort details" section edits the
hand-curated metadata fields (SNOW-500 — alternative name, operator,
website, lifts, runs, piste km, base/top elevation, typical season
open/close). Each input carries `data-resort-field="<model field>"`; the
server-side list they are validated against is
`apps.regions.forms.RESORT_DETAIL_FIELDS` (a `ResortDetailsForm` ModelForm, so
every constraint stays on the model). Adding a field means adding it in
both places and nowhere else.

One **Save** POSTs `{latitude, longitude, details}`, which sets
`geocode_source="MANUAL"`, `geocode_confidence=1.0`, `geocoded_at=now()`,
clears `needs_review`, and writes the detail fields — a save is a manual
confirmation of the marker's position as well as of the details. The
button reads "Saving…" and is disabled for the round trip, and a
confirmation line (`#edit-resorts-status`, `aria-live`) reports the
outcome before clearing itself; the readout's "Current" row catching up
with "Draft" is otherwise too quiet to notice. Run
`manage.py dump_resorts_fixture --commit` afterwards to persist a session
of edits to git.

**Adding a resort.** The **New resort** button switches the same target
block into create mode: the placement surface, the paste box, the details
section and Save all behave as they do for an edit, but there is no row
behind it, so the panel also asks for `name` — the one value nothing else
can supply — and offers `canton`. There is deliberately **no region
picker**: the parent region is derived server-side from the placed pin
(`_region_for_point`), the same lookup that auto-rebinds an edited
resort's region.

**Canton is inherited, not required.** Left blank, it is taken from the
resorts already in the derived region (`_canton_for_region` — the most
common non-blank value, ties broken alphabetically). This is right for
all but border cases: across the curated set, 65 of the 66 regions
holding resorts have one canton between them, and the exception is a
resort recorded as `BE/VS` rather than a genuine disagreement. Sending a
canton overrides the inherited one, which is how a border case or a
mis-tagged region gets corrected. The one case that must ask is a region
with no resorts to read from — `400 invalid_identity`, naming the region.
Nothing derives canton from geometry: the polygons are EAWS warning
regions, not cantonal boundaries.

**What is mandatory is marked.** Name carries an asterisk and
`aria-required`; the canton placeholder reads `auto`; and a hint line
under the readout names whatever is still outstanding ("Needs a name and
a pin.") rather than leaving a disabled Save button to be interpreted. A pin in a no-coverage gap is refused with
`400 no_region` rather than creating a row with a guessed parent
(`Resort.region` is not nullable), and a name that already exists in the
derived region is refused with `409 duplicate_name` — nearly always a
double-click rather than a genuine second resort. Canton is asked for
because there is no cantonal geometry to read it from; the region
polygons are EAWS warning regions, not cantons. On success the new row is
spliced into the in-memory catalogue and selected, so the operator
continues in the ordinary edit flow on the resort they just added.

A created resort lives only in the environment's own database (see
[`docs/decisions/resorts-are-editable-data.md`](decisions/resorts-are-editable-data.md)):
run `dump_resorts_fixture --commit` to carry it to other worktrees and
CI, and add it to `apps/regions/data/resorts.tsv` so the next
`import_resorts` reconciliation does not delete it as an unlisted row.

| URL | Name | Method | Notes |
|-----|------|--------|-------|
| `/api/edit/resorts/queue/` | `api:edit_resorts_queue` | GET | Flag-gated. Returns `{all_resorts, sub_regions}`, ordered `region_id ASC, name ASC` so the panel can group rows by L2 area (e.g. `CH-41`). Each entry carries a `details` object holding every `RESORT_DETAIL_FIELDS` value, so selecting a row needs no second fetch. |
| `/api/edit/resorts/<int:resort_id>/save/` | `api:edit_resort_save` | POST | Flag-gated. JSON body `{latitude, longitude, details?}`; coordinates outside `_SWISS_BBOX` are hard-rejected with 400. `details` is optional and may be partial — an omitted key keeps its stored value. An invalid field returns `400 {"error": "invalid_details", "fields": {…}}` and writes nothing at all, coordinates included. |
| `/api/edit/resorts/create/` | `api:edit_resort_create` | POST | Flag-gated. JSON body `{name, canton?, latitude, longitude, details?}`; same coordinate and `details` rules as `save`. The parent region comes from the pin; an omitted `canton` is inherited from that region's existing resorts. Returns `201` with the same body shape `save` answers with (a catalogue entry plus geocode provenance). Errors: `400 invalid_identity` (blank/over-long name, or a canton the region cannot supply), `400 no_region`, `409 duplicate_name`. |

All three endpoints 404 when the `edit_map` flag is inactive
(`_require_edit_map_flag()` in `apps/public/api.py` raises `Http404`). The
page itself silently falls back to the normal map when `?edit=resorts`
is set without the flag (`apps/public/views.py`), so the URL is safe to
bookmark.

Coordinate-ordering pitfall (called out in `static/js/map_edit_resorts.js`):

- DB columns: `latitude`, `longitude`.
- JSON wire format: `{"latitude": …, "longitude": …}` (keyed by name — unambiguous).
- GeoJSON: `coordinates: [longitude, latitude]` per RFC 7946.
- MapLibre marker: `marker.getLngLat()` returns `{lng, lat}` (note `lng`).

### Persisting edits — `dump_resorts_fixture`

Edits land in the local SQLite only. Run the dump command after a session
of placements to regenerate the seed fixture at
`apps/regions/fixtures/resorts.json`, which is what carries them to other
worktrees and CI:

```bash
uv run python manage.py dump_resorts_fixture          # dry-run, prints diff
uv run python manage.py dump_resorts_fixture --commit # writes the file
git diff apps/regions/fixtures/resorts.json                    # review
```

The dump uses `use_natural_foreign_keys=True` (so `region` round-trips
as `["CH-4115"]` not a numeric pk), pretty-prints with `indent=2`, and
orders by pk — the same shape as the existing fixture. Without the
dump step, edits live only on the operator's laptop and silently
disappear on `loaddata` re-runs. Mirrors `refresh_eaws_fixtures`'s
safe-by-default convention (read-only without `--commit`).

No deploy loads `resorts.json` — production coordinates are edited in that
environment, not shipped from git
([`resorts-are-editable-data`](decisions/resorts-are-editable-data.md)).
