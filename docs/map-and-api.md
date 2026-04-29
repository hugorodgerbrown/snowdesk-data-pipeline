# Map page and JSON API

`/map/` (`public:map`) renders a MapLibre GL JS choropleth of Swiss avalanche
regions. Tapping a region opens a bottom sheet with today's danger rating,
resort list, and a CTA to the full bulletin. The template (`public/templates/public/map.html`)
is standalone — it does not extend `base.html`. Static assets are
`static/js/map.js` and `static/css/map.css`.

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
active timelapse stops cleanly. See [`offline-map.md`](offline-map.md)
for the picker × offline-precache interaction.

**Season scrubber and timelapse**: a horizontal scrubber sits at the
bottom of the map. The thumb defaults to today's position within the
Nov–May window; dragging recolours every region from the
`/api/season-ratings/` payload to show how danger evolved on the
selected date, and pressing the play button steps through the season as
a timelapse. The drawer (when open) follows the scrubber via the
`snowdesk:date-changed` event, fetching `/api/region/<id>/summary/?d=…`
so the bulletin shown matches the scrubbed-to date. The season-ratings
payload is fetched lazily on first interaction and cached for the
session via `getSeasonRatings()` in `static/js/map.js` — first scrub
pays the round-trip; subsequent scrubs and timelapse playback render
from the cache.

**Route ordering**: `/map/` is registered before `<str:region_id>/` in
`public/urls.py`. Do not reorder these — Django matches URL patterns
top-to-bottom and the generic region pattern would swallow `/map/` if it
appeared first.

**JSON API** — plain `JsonResponse` views, no DRF. Mounted at `/api/` in
`config/urls.py` under the `api:` namespace (`public/api_urls.py`):

| URL | Name | Response |
|-----|------|----------|
| `GET /api/today-summaries/` | `api:today_summaries` | `{region_id: {rating, subdivision, problem, elevation, aspects, valid_from, valid_to, name}}` |
| `GET /api/season-ratings/` | `api:season_ratings` | `{date_iso: {region_id: rating_int}}` — whole-season choropleth source for the scrubber + timelapse on `/map/`. Compact int encoding (`0=no_rating`, `1=low`, … `5=very_high`) keeps the payload small. |
| `GET /api/resorts-by-region/` | `api:resorts_by_region` | `{region_id: [resort_name, …]}` — alphabetical; regions without resorts omitted |
| `GET /api/resorts.geojson` | `api:resorts_geojson` | GeoJSON FeatureCollection of geocoded resorts (Points; `[lon, lat]` per RFC 7946); properties `id`, `name`, `region_id`, `needs_review` |
| `GET /api/regions.geojson` | `api:regions_geojson` | GeoJSON FeatureCollection from `Region.boundary` (L4 fixture regions); each feature has `properties.id` + `properties.name` |
| `GET /api/major-regions.geojson` | `api:major_regions_geojson` | GeoJSON FeatureCollection of L1 EAWS major regions (e.g. `CH-4`, `CH-5`) with `properties.id` + `properties.name`. |
| `GET /api/sub-regions.geojson` | `api:sub_regions_geojson` | GeoJSON FeatureCollection of L2 EAWS sub-regions (e.g. `CH-41`, `CH-42`) with `properties.id` + `properties.name`. |
| `GET /api/region/<region_id>/summary/` | `api:region_summary` | `{peek, expanded}` — pre-rendered HTML fragments for the map drawer. `peek` is the compact bottom-sheet card; `expanded` is the full bulletin detail. Honours `?d=YYYY-MM-DD` so the drawer can show any scrubbed-to date. |
| `GET /api/offline-manifest/map/` | `api:offline_manifest_map` | `{version, urls[]}` — precache manifest consumed by `static/js/sw.js` (see [`offline-map.md`](offline-map.md)) |

`today-summaries` uses the same `_select_default_issue` helper as the bulletin
page (morning-update-wins-over-previous-evening), so the map and bulletin views
always agree on which issue to show. Regions with no covering bulletin today are
absent from the response; the map fill layer treats absence as `no_rating`.
Stale/errored render models (`version: 0`) resolve to `rating: "no_rating"`.

The shared top-nav partial used on the map and other public pages is
documented separately in [`nav_implementation_spec.md`](nav_implementation_spec.md).

## Edit-resorts mode (SNOW-74) — DEBUG only

`/map/?edit=resorts` enters resort-coordinate-edit mode when
`settings.DEBUG` is `True`. The page renders a right-hand panel with a
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
| `/api/edit/resorts/queue/` | `api:edit_resorts_queue` | GET | DEBUG-only. Returns `{queue, all_resorts}` — queue ordered `region_id ASC, name ASC` so the panel can group rows by L1 area (e.g. `CH-4`). `needs_review` rows still surface a ⚠ in the panel; they are no longer a sort key. |
| `/api/edit/resorts/<int:resort_id>/coords/` | `api:edit_resort_save_coords` | POST | DEBUG-only. JSON body `{latitude, longitude}`; coordinates outside `_SWISS_BBOX` are hard-rejected with 400. |

Both endpoints 404 when `DEBUG=False` (URL-level guard plus inline
`_require_debug()` — belt-and-braces). The page itself silently falls
back to the normal map when `?edit=resorts` is set without DEBUG, so
the URL is safe to bookmark.

Coordinate-ordering pitfall (called out in `static/js/map_edit_resorts.js`):

- DB columns: `latitude`, `longitude`.
- JSON wire format: `{"latitude": …, "longitude": …}` (keyed by name — unambiguous).
- GeoJSON: `coordinates: [longitude, latitude]` per RFC 7946.
- MapLibre marker: `marker.getLngLat()` returns `{lng, lat}` (note `lng`).

### Persisting edits — `dump_resorts_fixture`

Edits land in the local SQLite, not the source-of-truth fixture in git.
Run the dump command after a session of placements to regenerate
`pipeline/fixtures/resorts.json`:

```bash
poetry run python manage.py dump_resorts_fixture          # dry-run, prints diff
poetry run python manage.py dump_resorts_fixture --commit # writes the file
git diff pipeline/fixtures/resorts.json                   # review
```

The dump uses `use_natural_foreign_keys=True` (so `region` round-trips
as `["CH-4115"]` not a numeric pk), pretty-prints with `indent=2`, and
orders by pk — the same shape as the existing fixture. Without the
dump step, edits live only on the operator's laptop and silently
disappear on `loaddata` re-runs. Mirrors `refresh_eaws_fixtures`'s
safe-by-default convention (read-only without `--commit`).
