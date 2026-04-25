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

**Route ordering**: `/map/` is registered before `<str:region_id>/` in
`public/urls.py`. Do not reorder these — Django matches URL patterns
top-to-bottom and the generic region pattern would swallow `/map/` if it
appeared first.

**JSON API** — plain `JsonResponse` views, no DRF. Mounted at `/api/` in
`config/urls.py` under the `api:` namespace (`public/api_urls.py`):

| URL | Name | Response |
|-----|------|----------|
| `GET /api/today-summaries/` | `api:today_summaries` | `{region_id: {rating, subdivision, problem, elevation, aspects, valid_from, valid_to, name}}` |
| `GET /api/resorts-by-region/` | `api:resorts_by_region` | `{region_id: [resort_name, …]}` — alphabetical; regions without resorts omitted |
| `GET /api/regions.geojson` | `api:regions_geojson` | GeoJSON FeatureCollection from `Region.boundary`; each feature has `properties.id` + `properties.name` |
| `GET /api/offline-manifest/map/` | `api:offline_manifest_map` | `{version, urls[]}` — precache manifest consumed by `static/js/sw.js` (see [`offline-map.md`](offline-map.md)) |

`today-summaries` uses the same `_select_default_issue` helper as the bulletin
page (morning-update-wins-over-previous-evening), so the map and bulletin views
always agree on which issue to show. Regions with no covering bulletin today are
absent from the response; the map fill layer treats absence as `no_rating`.
Stale/errored render models (`version: 0`) resolve to `rating: "no_rating"`.

The shared top-nav partial used on the map and other public pages is
documented separately in [`nav_implementation_spec.md`](nav_implementation_spec.md).
