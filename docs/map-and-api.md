---
name: map-and-api
description: / (public:home) MapLibre choropleth, scrubber, overlays, /api/ endpoints (ratings, geojson, summary, groupings, region-basemap-tiles)
status: current
last-reviewed: 2026-07-26
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
module — unlike the `OVERLAY_LAYER_IDS` lists in `map.js`, which must
enumerate ids because they map *menu rows* to layers.

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
| `GET /api/regions.geojson` | `api:regions_geojson` | GeoJSON FeatureCollection from `Region.boundary` (L4 fixture regions); each feature has `properties.id` + `properties.name`, plus `properties.download` (SNOW-521 — see below) when the region has a precomputed offline-basemap size. |
| `GET /api/major-regions.geojson` | `api:major_regions_geojson` | GeoJSON FeatureCollection of L1 EAWS major regions (e.g. `CH-4`, `CH-5`) with `properties.id` + `properties.name`. Never carries `properties.download` — offline-basemap download is a MicroRegion-only feature (SNOW-521). |
| `GET /api/sub-regions.geojson` | `api:sub_regions_geojson` | GeoJSON FeatureCollection of L2 EAWS sub-regions (e.g. `CH-41`, `CH-42`) with `properties.id` + `properties.name`. Never carries `properties.download`, same as major-regions.geojson above. |
| `GET /api/region-basemap-tiles/?id=<region_id>` | `api:region_basemap_tiles` | The full precomputed `basemap_download` blob (incl. `z` tile-index ranges) for one MicroRegion (`MicroRegion.region_id` only — SNOW-521). 400 on a missing `?id=`; 404 for an unknown id or a region with no computed blob yet. Fetched on demand — see [`offline-map.md`](offline-map.md#offline-overlay-caches--download-basemap-snow-492-snow-521) for the full download flow. |
| `GET /api/region/<region_id>/summary/` | `api:region_summary` | `{html, level}` — `html` is the server-rendered MapLibre Popup snippet (danger-rating chip + geographic breadcrumb); `level` is the rating string the JS uses to stamp `data-level` on the popup container for the border colour. Honours `?d=YYYY-MM-DD` so the popup can show any scrubbed-to date; returns 400 on a malformed value. **Currently unused by the client** — the region popup lost its trigger (see the intro above); the endpoint is kept alongside `openRegionPopup` pending a decision on a replacement detail surface. |
| `GET /api/bulletin-groupings.geojson` | `api:bulletin_groupings_geojson` | `{"type":"FeatureCollection","features":[…]}` — a **single day's** dissolved bulletin boundaries. `?d=YYYY-MM-DD` is **required** (400 `date_required` if absent, 400 `malformed date` on a bad value). Each feature's geometry is the dissolved outer boundary of all L4 micro-regions sharing that bulletin; `properties` carries `bulletin_id`, `date`, and `countries` (sorted ISO-2 list). Accepts optional `?country=ch\|fr\|at\|it`; filters by membership in the `countries` list (a cross-border bulletin with `["AT","IT"]` appears for both `?country=at` and `?country=it`). Server-side `cache.get_or_set` keyed on `(country, date)` (5 min). **`Cache-Control` is date-aware (SNOW-526):** `bulletins.services.settled.earliest_mutable_date()` derives a settled/unsettled threshold from the fetcher registry (`bulletins.services.slf_fetcher.get_sources()`), memoised at the call site (`public.api._cached_earliest_mutable_date()`, 60s) to avoid a per-`BulletinSource` DB query on every request; a settled `?d=` gets `public, max-age=604800, immutable`, otherwise `public, max-age=300` (unchanged). The `immutable` token is also `sw.js`'s signal to persist the response for offline use — see `docs/decisions/date-aware-cache-policy.md`. **Why single-date:** the endpoint previously returned the whole season keyed by date in one payload; once the historical backfill landed, serialising every day's dissolved geometry at once pushed the web worker past its 512 MB limit (SNOW-323 follow-up). The JS overlay ("Bulletin groupings" — the boundary now draws alongside the L4 layer, SNOW-506; SNOW-521 removed the standalone `data-overlay-key="l3"` layers-menu row) fetches one day at a time via `fetchBulletinGroupingsForDate(dateKey)` (no `?country=` filter, so cross-border rows are present), memoising each date for the session. It draws the boundary only once the scrubber **settles** (`GROUPINGS_SETTLE_MS = 250`), blanking the layer during active drag/playback so it neither thrashes the network nor lags a frame behind the choropleth. The MapLibre layer uses an array-membership filter (`['in', c, ['get','countries']]`) instead of the scalar `match` filter used by L1/L2, because `countries` is a JSON list not a string. |
| `GET /api/community-reports.geojson` | `api:community_reports_geojson` | `{"type":"FeatureCollection","features":[…]}` — anonymised, clustered "Community reports" overlay (SNOW-419). Covers `FieldObservation` rows from the last 48 hours. Each feature's `coordinates` are `[lon, lat]` rounded to 3 dp (~80–110 m); `properties` carries `type` (`OBSERVATION_TYPE` value), `type_label` (display label), `observed_at` (ISO, floored to the nearest 15 min), and `region_name` (or `null`). Never serialises `latitude`/`longitude` at full precision, `gps_*`, `accuracy_radius_km`, `user`, or the row's pk. `Cache-Control: private, no-store` — unlike the other geojson endpoints it is **not** publicly cacheable and **not** in `_POSTHOG_EXEMPT_PATHS` (SNOW-459); public caching is tracked separately (SNOW-469). It carries a 120s client-side freshness window via `X-Data-Max-Age`. The JS overlay (`data-overlay-key="community_reports"`, default **off**) clusters the source client-side (`cluster: true`) and fades pins by age via a client-computed `_ageOpacity` feature property (no MapLibre "now" expression exists). |

**Per-region offline-basemap sizing (SNOW-521)**: `properties.download` on
`regions.geojson` (L4 only — MajorRegion/SubRegion never carry it) is a
small, flat summary projected from the region's precomputed
`basemap_download` field (`regions/services/basemap_tiles.py`,
`blob_summary`, populated by `manage.py compute_basemap_download`) —
never computed at request time, since region geometry and the basemap
tile grid are both static:

```json
"download": {"count": 329, "mb": 33, "over_ceiling": false, "centre_tile": {"z": 14, "x": 8520, "y": 5820}}
```

The whole `download` key is omitted when the region has no computed
blob yet. The full blob (incl. the `z` tile-index ranges the client
expands into tile URLs) is only fetched — via `region-basemap-tiles`
above — when the user clicks the region's download icon. See
[`offline-map.md`](offline-map.md) for the full client-side download
flow.

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

Edits land in the local SQLite only. Run the dump command after a session
of placements to regenerate the seed fixture at
`regions/fixtures/resorts.json`, which is what carries them to other
worktrees and CI:

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

No deploy loads `resorts.json` — production coordinates are edited in that
environment, not shipped from git
([`resorts-are-editable-data`](decisions/resorts-are-editable-data.md)).
