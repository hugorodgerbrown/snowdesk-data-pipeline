---
name: glossary
description: Domain term → code symbol map — CAAML, DPBRA, massif, Bulletin/RegionBulletin, render model, day rating, sentinels
status: current
last-reviewed: 2026-08-05
---

# Glossary — domain terms to code symbols

One line per term: what it means here, and where it lives in code. When a doc,
a template, and a conversation use different words for the same thing, this
file states the canonical name. Update it whenever a new domain term acquires
a code symbol.

## Formats and sources

| Term | Meaning | Code |
|------|---------|------|
| CAAML | Open avalanche-bulletin interchange standard ("CAA Markup Language"); this project consumes **CAAML v6 JSON** | consumed throughout `apps/bulletins/` |
| SLF | Swiss Institute for Snow and Avalanche Research — primary provider | `apps/bulletins/services/slf_fetcher.py` |
| ALBINA | EUREGIO (Tyrol/South Tyrol/Trentino) avalanche service | `apps/bulletins/services/albina_fetcher.py` |
| Météo-France | French provider; serves DPBRA XML, not CAAML | `apps/bulletins/services/meteofrance_fetcher.py` |
| DPBRA | Météo-France's public avalanche-risk-bulletin product (XML, one document per massif) at `public-api.meteofrance.fr/public/DPBRA/v1/` | translated to CAAML v6 JSON by `apps/bulletins/services/meteofrance_translator.py` |
| Source | The provider a bulletin was ingested from, stored on the indexed `Bulletin.source` column and named by the `Bulletin.Source` TextChoices — `"SLF"`, `"ALBINA"`, `"METEOFRANCE"` (upper-cased by SNOW-582). Set at ingest by `upsert_bulletin` from `detect_source`, never read back out of `render_model["source"]` | `apps/bulletins/models.py`; backfilled by `backfill_bulletin_source` |
| Fidelity | The rule that every field the provider published reaches a rendered surface, and anything excluded carries a written reason. One row per dotted CAAML path across the nine sentinels | `tests/sentinels/fidelity.py` (`Rendered` / `Excluded`), checked by `bin/fidelity-lint` and `tests/sentinels/test_fidelity.py` |
| GeoJSON Feature envelope | Raw bulletins are stored wrapped as `{type: "Feature", geometry: null, properties: <raw CAAML>}` | `upsert_bulletin()` in `apps/bulletins/services/slf_fetcher.py` (shared by all fetchers) |
| EAWS | European Avalanche Warning Services — defines the region hierarchy and the 1–5 danger scale | `apps/regions/fixtures/eaws_{CH,AT,FR,IT}.json` |
| Massif | Météo-France's mountain-region unit (e.g. `CHABLAIS`); slug → `FR-NN` region id | `apps/bulletins/services/meteofrance_massifs.py` (`SLUG_TO_CODE`, `slug_to_region_id()`) |

## Regions

EAWS hierarchy: L1 (`MajorRegion`) → L2 (`SubRegion`) → L4 (`MicroRegion`);
L3 is deliberately skipped. All in `apps/regions/models.py`.

| Term | Meaning |
|------|---------|
| MajorRegion | L1, e.g. `CH-4` (Valais); `prefix` unique, boundary derived from children |
| SubRegion | L2, e.g. `CH-41` (Lower Valais); FK to major |
| MicroRegion | L4 warning region, e.g. `CH-4115` or `FR-68` — the unit bulletins, ratings, and subscriptions attach to; `region_id` unique |
| Resort | Ski resort geocoded onto a MicroRegion |
| Region id formats | `CH-4115` (4-digit), `FR-01` (2-digit), `AT-07-23-02` / `IT-32-BZ-15` (multi-level) |
| Resort page | Public detail page for one Resort at `/resorts/<id>/<slug>/` — danger chip, bulletin link, favourite toggle (SNOW-504). `public:resort` route; `resort_detail()` in `apps/public/views.py`; template `apps/public/templates/public/resort.html` |
| Resort tier | Curated map prominence for one resort — `CORE` / `STANDARD` / `MINOR`, driving pin radius on the map (SNOW-543). Stored, not derived: scale is the wrong axis, so a curator can promote a small high area. `Resort.Tier`; sheet column `tier`; `properties.tier` on `/api/resorts.geojson`; consumed by `installResortsLayer` in `static/js/map.js` |
| Why it matters | One curated sentence on why a backcountry skier should care about a resort — terrain, access, reputation; the thing piste km cannot say (SNOW-542). `Resort.why_it_matters`; sheet column `why_it_matters`; rendered by `apps/public/templates/public/partials/_resort_why_it_matters.html` |

## Bulletins and ratings

| Term | Meaning | Code |
|------|---------|------|
| Bulletin | One CAAML bulletin from one provider for one validity window; `bulletin_id` globally unique; holds `raw_data` + `render_model` | `apps/bulletins/models.py` |
| RegionBulletin | Through table (bulletin, MicroRegion); snapshots `region_name_at_time` because region names drift | `apps/bulletins/models.py` |
| PipelineRun | One execution of an ingestion run — status, timings, created/updated/failed counts | `apps/bulletins/models.py` |
| Render model | Versioned presentation JSON built at ingest so templates contain no derivation logic; `RENDER_MODEL_VERSION = 8` | `apps/bulletins/services/render_model.py` (`build_render_model()`); stored on `Bulletin.render_model` |
| Danger rating | EAWS 1–5 scale (low → very_high) plus `no_snow` / `no_rating` | `DangerRatingValue` in `apps/bulletins/schema.py`; numeric map `_DANGER_NUMBER` in `render_model.py` |
| Subdivision | SLF's +/=/− refinement of a level (e.g. `4-`) | carried through render model and `RegionDayRating.subdivisions` |
| RegionDayRating | Denormalised per-(region, date) min/max rating from the authoritative bulletin; feeds the calendar and CSV export | `apps/bulletins/models.py`; built by `apps/bulletins/services/day_rating.py` |
| Peak rating | The single rating shown when a compressed view (choropleth, tooltip, calendar tile) must collapse a split day — see [compressed-views-rating-rule.md](compressed-views-rating-rule.md) | `apps/public/headlines.py` |
| Day character | Five-way classification of a bulletin day (stable / manageable / hard_to_read / widespread / dangerous) | `compute_day_character()` in `apps/bulletins/services/render_model.py`; spec in [day_character_rules_spec.md](day_character_rules_spec.md) |
| Day summary | The one-line explainer beside the day-character label, selected per bulletin from an 80-cell copy matrix | `summary_for()` in `apps/bulletins/services/day_summary.py`; see [day-summary.md](day-summary.md) |
| Movement | How a day's danger level moves across its windows — static / rising / easing / shifting | `classify_movement()` in `apps/bulletins/services/day_summary.py` |
| Readability | Whether a day's problems leave surface evidence — readable / hidden / mixed / quiet | `classify_readability()` in `apps/bulletins/services/day_summary.py` |
| Avalanche problem | EAWS problem token (new_snow, wind_slab, …) with rating, aspects, elevation | `AvalancheProblem` dataclass in `apps/bulletins/schema.py` |
| Elevation band | Per-rating altitude banding (ALBINA / Météo-France only — SLF has none) | `RegionDayRating.bands` JSON |
| Unscheduled bulletin | Out-of-cycle update flagged by the provider | `Bulletin.unscheduled` |
| Provider row | A row in the map's layers menu "Bulletins" section — one WARNING SERVICE, not one country. ALBINA publishes for AT and IT, so its single row switches both codes: the grouping is declared once in `COUNTRY_GROUPS` and projected into the DOM as `data-country-codes` (SNOW-658) | `COUNTRY_GROUPS` / `countryCodesFor` / `overlayKeyForCountry` in `static/js/map_state.js`; guarded by `tests/public/test_map_country_groups.py` |
| Panel overlay switch | The "Show X on the map" switch inside a map panel — the sheet-owned replacement for a layers-menu row, for user-generated data (downloads, favourites, community reports). Drives a frozen `window.pwa*Overlay` bridge in `map.js`; carries no sync dot (SNOW-658) | `templates/includes/_map_overlay_toggle.html`; `window.pwaFavouritesOverlay` / `window.pwaCommunityReportsOverlay` / `window.pwaDownloadedOverlay` in `static/js/map.js` |
| Bulletin fill | The bulletin DATA painted onto micro-regions — the `regions-fill` choropleth plus `bulletin-groupings-line`. Split from the Micro regions row (geography only) by SNOW-656. Strength is one of five user-chosen opacity steps, 0 being off; mutually exclusive with the downloaded-areas overlay | `static/js/layer_visibility_core.js`; `#map-fill-toggle` / `data-bulletins-step`; [bulletin-fill-is-a-user-choice.md](decisions/bulletin-fill-is-a-user-choice.md) |

## Weather

| Term | Meaning | Code |
|------|---------|------|
| WeatherSnapshot | Open-Meteo weather for one (region, date): WMO `weather_code` 0–99, sunrise/sunset, plus the nullable daily aggregates `temperature_2m_max` / `temperature_2m_min` (°C) and `snowfall_sum` (cm) added in SNOW-571 | `apps/weather/models.py`; fetched by `apps/weather/services/weather_fetcher.py` |
| is_day projection | Render-time check that "now" falls between that region's sunrise and sunset — never stored | `is_day()` in `apps/weather/services/weather_display.py` |
| Bulletin header | Context dict for `templates/includes/bulletin_header.html` ("weather header" is its historical name) | `bulletin_header_context()` in `apps/weather/services/weather_display.py` |
| Weather panel | Shared bucket-coloured weather partial (SNOW-509); included by both the bulletin masthead and the resort page | `templates/includes/_weather_panel.html` |
| Forecast convergence | How the forecast for one day changed as that day approached; read from the retained per-issue-date series rather than the single surviving day-of row (SNOW-575) | `ForecastCellWeatherHistory.objects.convergence_for()` in `apps/weather/models.py` |
| Lead days | `valid_for_date - issued_date` — how far ahead a forecast was looking when it was fetched. `0` is the day-of view | `lead_days` on `ForecastCellWeatherHistory` |
| Issued date | The run anchor a forecast was fetched on, part of the history key so one forecast day can be stored once per issue date | `issued_date` on `ForecastCellWeatherHistory` |

## Field observations

| Term | Meaning | Code |
|------|---------|------|
| FieldObservation | A GPS-gated avalanche-signal report submitted by an account from the map page (SNOW-324); stores observation types, coordinates, and region match | `apps/observations/models.py` |

## Routes

| Term | Meaning | Code |
|------|---------|------|
| Route | A polyline a user imported from a GPX upload (SNOW-685) — `points` as `[[lon, lat, ele], …]` in GeoJSON axis order, plus denormalised `distance_m` / `ascent_m` / `descent_m` / `point_count` / `bounds`. The uploaded file is parsed and discarded ([why](decisions/gpx-uploads-are-parsed-not-stored.md)) | `apps/routes/models.py`; ingest in `apps/routes/services/gpx.py` |
| Elevation profile | The chart of a Route's height against distance along it, drawn in the route's map popup. Read from the GPX's own `<ele>` — the third ordinate of each stored coordinate — with no DEM lookup, no smoothing and no interpolation across a point whose `<ele>` was missing (the line breaks instead) | `static/js/elevation_profile_core.js`; mounted by `appendElevationProfile` in `static/js/map.js` |
| Start / finish markers | The two symbols at a Route's ends on the map — a filled dot in the route colour where the track starts, a checkered flag where it ends. Shown from zoom 10, matching the resort labels. A closed track (an out-and-back) gets the flag alone, since both ends are the same place | `static/js/route_markers_core.js`; the `routes-endpoints` layer in `static/js/map.js` |
| Ascent / descent | A Route's total climb and total drop in metres, both positive magnitudes, measured independently at ingest on the full-resolution track and never netted against each other. Null — not zero — when the source GPX carried no elevation at all | `Route.ascent_m` / `Route.descent_m`; `_total_ascent_descent_m` in `apps/routes/services/gpx.py` |

## Coordinates and locations

Which coordinate on which model is exact, approximate or derived:
[`docs/locations.md`](locations.md).

| Term | Meaning | Code |
|------|---------|------|
| Location | The domain primitive: a point on the map that we keep. A resort's village, mid-station or peak; a favourite; an observation; a region centroid. **A curated place is a `Location` with a `name`** — there is no separate curated-place model, so Mont Fort is one row four resorts reference (SNOW-700) | `Location` in `apps/locations/models.py`; `docs/decisions/location-is-the-primitive.md` |
| Anonymous location | A `Location` carrying no `name` and no `kind` — minted from a favourite or an observation. Naming is a curation act | `LocationQuerySet.anonymous()` / `.named()` |
| Role vs kind | `Location.kind` describes the place (Mont Fort is a peak whoever is looking); `ResortLocation.role` describes what it is *to one resort*. Attelas can be one resort's top and another's mid-station — which is why the join is a many-to-many | `Location.KIND` / `ResortLocation.ROLE` in `apps/locations/models.py` |
| Fetch cell | A `ForecastCell` — the quantised cell at which Open-Meteo is called, **not a place**. Its `latitude`/`longitude` are whichever pin minted the cell; identity is the `(lat_cell, lon_cell, elevation_band)` triple. Was `ForecastPoint` until SNOW-703; the table is still `bulletins_forecastpoint` | `ForecastCell` in `apps/weather/models.py`; `docs/decisions/forecast-point-quantisation.md` |
| Reuse threshold | 750 m horizontal / 150 m vertical — how close a pin must be to share an existing fetch cell rather than mint its own, so a pin near a grid boundary still shares the neighbouring row | `REUSE_HORIZONTAL_THRESHOLD_M` / `REUSE_ELEVATION_THRESHOLD_M` in `apps/weather/services/forecast_cells.py` |
| Referent | Anything holding an FK to a fetch cell. A cell with none falls out of `active()`, stops being fetched, and is deleted **with its stored weather** by `prune_forecast_points` | `ForecastCellQuerySet.active()` / `.inactive()` in `apps/weather/models.py` |
| Report location vs raw fix | On a field observation, `latitude`/`longitude` is where the report says it happened (possibly dragged); `gps_latitude`/`gps_longitude` is the device's own fix. The gap is precision, never anonymisation | `FieldObservation` in `apps/observations/models.py` |
| Region centroid | `MicroRegion.centre` — the polygon's centroid, representing the region rather than any place anyone goes. Region weather means "somewhere in this region" | `centre` on `MicroRegion` / `SubRegion` / `MajorRegion`; computed by `refresh_eaws_fixtures` |
| Village coordinate | `Resort.latitude`/`longitude` — the geocoder's hit for the resort's *name*, which lands in the village (Verbier 1436 m against terrain to 3330 m), not on the terrain | `Resort` in `apps/regions/models.py` |

## Terrain

| Term | Meaning | Code |
|------|---------|------|
| Slope angle overlay | The map's steepness raster (SNOW-691) — swisstopo's `ch.swisstopo.hangneigung-ueber_30`, banded to the SLF classification (30–35 / 35–40 / 40–45 / 45–50 / >50°). Under 30° is unshaded | `static/js/slope_overlay_core.js`; `installSlopeLayer` in `static/js/map.js`; `SLOPE_TILE_URL` in `config/settings/base.py` |
| Coverage rectangle | The slope raster's declared extent, `5.140242, 45.398181` → `11.47757, 48.230651`. Used as the source's `bounds` and to disable the layers-menu row outside it, because unshaded ground *outside* it means "not surveyed" rather than "under 30°" | `COVERAGE_BOUNDS` / `coversPoint` in `static/js/slope_overlay_core.js` |

## Subscriptions and tracking

| Term | Meaning | Code |
|------|---------|------|
| Account | The single public-user identity (`OneToOne` to `auth.User`, SNOW-514 collapsed the former `Subscriber` model into this). Auto-created at every public entry point (subscribe, sign-in, register). `is_verified` is the sole "email proven reachable" gate, set by every email-proving link. `AUTH_USER_MODEL` is Django's built-in `auth.User`; `Account` is a domain profile, not the user model | `apps/accounts/models.py` |
| Subscription | (Account, MicroRegion) pair driving bulletin emails | `apps/accounts/models.py` |
| Signed token | `TimestampSigner` tokens for account access (expiring) and unsubscribe (permanent, encodes `email\|region_id`) | `apps/accounts/services/token.py` |
| PasskeyCredential | WebAuthn platform passkey for an `auth.User` (FK to `User`, not `Account` — any authenticated user, including staff without an Account profile, can register one) | `apps/accounts/models.py` |
| PushSubscription | Web Push endpoint (spike) | `apps/accounts/models.py` |
| BulletinShare / BulletinShareClick | Tokenised short share URL and its per-follow click log | `apps/bulletins/models.py` |
| RequestLog | Request-context snapshot (geo, UA, referer) captured at sign-up/sign-in/account/share-click | `apps/core/models.py` |

## Frontend and design system

| Term | Meaning | Code |
|------|---------|------|
| Overlay primitive | One of the four consolidated DS shapes — banner, toast, sheet, modal — sharing the `data-action="dismiss"` / `[data-overlay]` contract and the `--z-*` token scale | `templates/includes/_overlay_{banner,modal,sheet}.html`, `_toast.html`; `static/js/overlays.js`; [overlay-primitives.md](decisions/overlay-primitives.md) |

## Offline basemap downloads (PWA)

| Term | Meaning | Code |
|------|---------|------|
| Area id | The unit a pinned basemap download's Cache Storage bucket and budget record are both keyed on — `region-<region_id>` for a region download, `custom` for the one custom-area download (SNOW-586) | `areaIdForRegion()`, `CUSTOM_AREA_ID` in `static/js/basemap_download_core.js` |
| Download budget | The standing byte ceiling across every pinned area (`DOWNLOAD_BUDGET_MB`, 500 MB, device-local — overridable via `meta:app`'s `basemap.budgetMb`), distinct from `DOWNLOAD_CEILING_MB` (200 MB, per single run) (SNOW-586) | `planEviction()` in `static/js/basemap_download_core.js`; [per-area-pinned-basemap-caches.md](decisions/per-area-pinned-basemap-caches.md) |

## Testing

| Term | Meaning | Code |
|------|---------|------|
| Sentinels | Canonical per-provider payload examples — three cases per provider: **A** single-level single-problem, **B** structurally enhanced single rating, **C** split day + multi-problem | `tests/sentinels/{slf,albina,meteofrance}/` (each case has `source.json` + README) |
| Round-trip contract | Every sentinel must pass `build_render_model()` without raising; Météo-France `source.xml` must translate back to its committed `source.json` | `tests/sentinels/test_sentinel_round_trip.py` |
| Golden week | Seven consecutive **real** days (Mon 2026-02-09 → Sun 2026-02-15), all three providers, selected from the committed archives — the realistic corpus for testing behaviour *across* days, as opposed to the sentinels' one-example-per-structural-case | `apps/bulletins/services/golden_week.py`, loaded by `seed_test_week` |
| Target day | The day a bulletin *forecasts*, as opposed to the day it was published: a morning issue forecasts today, an evening issue tomorrow | `target_day_for_valid_from()` in `apps/bulletins/services/day_rating.py`; stored on `Bulletin.target_date`, set at ingest by `upsert_bulletin` and back-filled by `backfill_bulletin_target_dates` |
| Fixture days | Hand-picked real-world edge-case payloads (variable day, prose-only day, legacy no-publication-time, …) | `tests/fixtures/sample_*.json` |
