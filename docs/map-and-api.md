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

`today-summaries` uses the same `_select_default_issue` helper as the bulletin
page (morning-update-wins-over-previous-evening), so the map and bulletin views
always agree on which issue to show. Regions with no covering bulletin today are
absent from the response; the map fill layer treats absence as `no_rating`.
Stale/errored render models (`version: 0`) resolve to `rating: "no_rating"`.

## Navigation partial

All public pages include a shared top nav partial at
`templates/includes/nav.html`. It renders the "Snowdesk" wordmark (always
linking home) plus an optional chevron-back link controlled by two include
parameters:

```django
{# logo only — home, map #}
{% include "includes/nav.html" %}

{# logo + back link — bulletin, random_bulletins, season_bulletins #}
{% url 'public:map' as map_url %}
{% include "includes/nav.html" with back_url=map_url back_label="Map" %}
```

The `<nav>` spans full viewport width so its bottom border forms an
edge-to-edge rule; inner content sits in a 640px max-width container that
aligns with the bulletin body copy. See
[`nav_implementation_spec.md`](nav_implementation_spec.md) for the full spec.
