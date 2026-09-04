---
name: glossary
description: Domain term → code symbol map — CAAML, DPBRA, massif, Bulletin/RegionBulletin, render model, day rating, sentinels, Location, Weather
status: current
last-reviewed: 2026-09-04
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
| Bulletin fill | The bulletin DATA painted onto micro-regions — the `regions-fill` choropleth plus `bulletin-groupings-line`. Split from the Micro regions row (geography only) by SNOW-656. Strength is one of five user-chosen opacity steps, 0 being off. Was mutually exclusive with the downloaded-areas overlay until SNOW-663 made those squares a hatch the danger colour reads through; the two are independent now | `static/js/layer_visibility_core.js`; `#map-fill-toggle` / `data-bulletins-step`; [bulletin-fill-is-a-user-choice.md](decisions/bulletin-fill-is-a-user-choice.md) |

## Field observations

| Term | Meaning | Code |
|------|---------|------|
| FieldObservation | A GPS-gated avalanche-signal report submitted by an account from the map page (SNOW-324); stores observation types, coordinates, and region match | `apps/observations/models.py` |

## Routes

| Term | Meaning | Code |
|------|---------|------|
| Route | A polyline a user imported from a GPX upload (SNOW-685) — `points` as `[[lon, lat, ele], …]` in GeoJSON axis order, plus denormalised `distance_m` / `ascent_m` / `descent_m` / `point_count` / `bounds`. The uploaded file is parsed and discarded ([why](decisions/gpx-uploads-are-parsed-not-stored.md)) | `apps/routes/models.py`; ingest in `apps/routes/services/gpx.py` |
| RouteShare | A tokenised link handing one Route to another account (SNOW-764). Reusable and time-bounded (`settings.ROUTE_SHARE_MAX_AGE_DAYS`); claiming it COPIES the route onto the claimer's account and never transfers it, so the sharer keeps theirs | `apps/routes/models.py`; `apps/routes/services/shares.py` |
| Pending share | A `RouteShare` this browser has followed but not claimed. Held as a token in `request.session["route_shares"]`, which is why the routes panel and the routes map layer answer for an anonymous request ([why](decisions/route-share-pending-claim-in-session.md)). Drawn as the dashed `routes-line-pending` layer | `pending_shares()` in `apps/routes/services/shares.py`; `routes-line-pending` in `static/js/map.js` |
| Elevation profile | The chart of a Route's height against distance along it, drawn in the route's map popup. Read from the GPX's own `<ele>` — the third ordinate of each stored coordinate — with no DEM lookup, no smoothing and no interpolation across a point whose `<ele>` was missing (the line breaks instead) | `static/js/elevation_profile_core.js`; mounted by `appendElevationProfile` in `static/js/map.js` |
| Start / finish markers | The two symbols at a Route's ends on the map — a filled dot in the route colour where the track starts, a checkered flag where it ends. Shown from zoom 10, matching the resort labels. A closed track (an out-and-back) gets the flag alone, since both ends are the same place | `static/js/route_markers_core.js`; the `routes-endpoints` layer in `static/js/map.js` |
| Ascent / descent | A Route's total climb and total drop in metres, both positive magnitudes, measured independently at ingest on the full-resolution track and never netted against each other. Null — not zero — when the source GPX carried no elevation at all | `Route.ascent_m` / `Route.descent_m`; `_total_ascent_descent_m` in `apps/routes/services/gpx.py` |
| Download area | The DEFINITION of one offline basemap download on a user's account (SNOW-749) — a region id or a `[west, south, east, north]` bbox, plus the basemap key and a name, keyed by the client-minted `area_id` that also names the device's Cache Storage bucket. Never the bytes: those are per-browser, so an area on this list is not thereby available offline here | `apps/downloads/models.py` (`DownloadArea`); client in `static/js/downloads_sync.js`, merged by `reconcileAreas` in `static/js/basemap_manage_core.js` |
| `onDevice` / `synced` | The two independent facts every reconciled download area carries. `onDevice` means the tiles are in this browser's Cache Storage; `synced` means the account has a `DownloadArea` row for it. They are separate because "in your account" is not "available offline" — a synced-not-here area must never paint a green sync dot | `reconcileAreas` / `manageRows` in `static/js/basemap_manage_core.js` |

## Trips

| Term | Meaning | Code |
|------|---------|------|
| Trip | A route the organiser already owns, on a named day, meeting somewhere at a stated time (SNOW-819). One row with a roster, never a thing that gets copied — everyone on it is meeting each other, so they belong on the same row ([why](decisions/a-trip-is-one-object-with-a-roster.md)) | `Trip` in `apps/trips/models.py`; `create_trip` in `apps/trips/services/trips.py` |
| Trip snapshot | The route geometry and figures COPIED onto a `Trip` at creation and never re-read — `points`, `bounds`, `distance_m`, `ascent_m`, `descent_m`, `point_count`, `route_name`. It is what makes a trip survive the organiser renaming, re-uploading or deleting the route it came from | `Trip.points` and siblings; `_SNAPSHOT_FIELDS` in `apps/trips/services/trips.py` |
| Organiser | The account that created a trip. Derived from `Trip.created_by`, never a second column on the roster row — they hold a `TripParticipant` row like everybody else, so "everyone on this trip" is one relation | `Trip.created_by`; `is_organiser` in `apps/trips/views.py` |
| TripParticipant | One account on one trip. `(trip, user)` is unique, and the organiser's row is written at creation | `TripParticipant` in `apps/trips/models.py` |
| Meeting point | Where a trip's group meets — an anonymous `Location` minted for that trip alone, defaulting to the route's first coordinate. `PROTECT`, so the orphan sweep can ask the database whether anything still references it | `Trip.meeting_point`; `_meeting_coordinates` in `apps/trips/services/trips.py` |
| Save route | Copying a trip's SNAPSHOT into the viewer's own routes (SNOW-824) — available to anyone who can see the trip, participant or not. Saving does not join and joining does not save, and nothing links the copy back afterwards; the "already saved" state is an exact geometry match ([why](decisions/a-trip-share-is-a-page-not-a-pending-claim.md)) | `save_trip_route` / `already_saved` in `apps/trips/services/routes.py`; the write is `write_route_copy` in `apps/routes/services/shares.py` |
| Wall-clock day and time | A trip's `date` and `start_time`, stored as a plain date and time and never converted between timezones. "07:30" is what the organiser typed and what everyone standing at the lift station reads off their own watch | `Trip.date` / `Trip.start_time` |

## Coordinates and locations

Which coordinate on which model is exact, approximate or derived:
[`docs/locations.md`](locations.md).

| Term | Meaning | Code |
|------|---------|------|
| Location | The domain primitive: a point on the map that we keep. A resort's village, mid-station or peak; a favourite; an observation; a region centroid. **A curated place is a `Location` with a `name`** — there is no separate curated-place model, so Mont Fort is one row four resorts reference (SNOW-700) | `Location` in `apps/locations/models.py`; `docs/decisions/location-is-the-primitive.md` |
| Anonymous location | A `Location` carrying no `name` and no `kind` — minted from a favourite or an observation. Naming is a curation act | `LocationQuerySet.anonymous()` / `.named()` |
| Active location | A `Location` reachable from a `ResortLocation`, a `MicroRegion.centroid_location` or a `Favourite` — the set worth an Open-Meteo call, and the set a public feed may publish. **Explicitly excludes a location reachable only from a `FieldObservation`**: a field report must not mint a billable forecast (SNOW-759) | `LocationQuerySet.active()` in `apps/locations/models.py` |
| Weather | What was known about one `Location` on one day. One row per `(location, observed_on)`, immutable once that day is past; `forecast` holds the days after it *as known on it* | `Weather` in `apps/weather/models.py`; `docs/decisions/weather-is-one-immutable-location-row.md` |
| observed_on | The day a `Weather` row **is of** — not the day it was fetched (that is `fetched_at`). Today's row is refined in place by each of the four daily runs; a past one raises `ImmutableWeatherRowError` | `Weather.observed_on`; `upsert_weather` in `apps/weather/services/upsert.py` |
| Region centroid | The `Location` at a micro-region's centre — what anchors a region in the location estate so it can have weather. Anonymous by design: a centroid is not a place anyone goes | `MicroRegion.centroid_location`, filled by `link_region_centroid_locations`; read via `MicroRegion.centre_point()` |
| short_id | A `Location`'s external identifier: eleven URL-safe characters from `secrets.token_urlsafe(8)`, the key of `/weather/<short_id>/` and of `weather.geojson`'s features (SNOW-797). Opaque rather than a slug because ~461 of ~540 public locations are unnamed centroids; never all digits, so it cannot collide with the legacy integer route | `Location.short_id`; `ShortIdConverter` in `apps/locations/converters.py`; `backfill_location_short_ids` |
| Resort slug | A `Resort`'s stored, unique URL identifier — `/resorts/<slug>/` and `resorts.geojson`'s `id` (SNOW-796). Minted from the name on first save and **never regenerated on rename**: the page is indexed, and a changed slug is a broken URL | `Resort.slug`; `unique_resort_slug()` in `apps/regions/models.py`; `backfill_resort_slugs` |
| Role vs kind | `Location.kind` describes the place (Mont Fort is a peak whoever is looking); `ResortLocation.role` describes what it is *to one resort*. Attelas can be one resort's top and another's mid-station — which is why the join is a many-to-many | `Location.KIND` / `ResortLocation.ROLE` in `apps/locations/models.py` |
| Report location vs raw fix | On a field observation, `latitude`/`longitude` is where the report says it happened (possibly dragged); `gps_latitude`/`gps_longitude` is the device's own fix. The gap is precision, never anonymisation | `FieldObservation` in `apps/observations/models.py` |
| Region centroid | `MicroRegion.centre` — the polygon's centroid, representing the region rather than any place anyone goes | `centre` on `MicroRegion` / `SubRegion` / `MajorRegion`; computed by `refresh_eaws_fixtures` |
| Village coordinate | `Resort.latitude`/`longitude` — the geocoder's hit for the resort's *name*, which lands in the village (Verbier 1436 m against terrain to 3330 m), not on the terrain | `Resort` in `apps/regions/models.py` |

## Terrain

| Term | Meaning | Code |
|------|---------|------|
| Slope angle overlay | The map's steepness raster (SNOW-691) — swisstopo's `ch.swisstopo.hangneigung-ueber_30`, banded to the SLF classification (30–35 / 35–40 / 40–45 / 45–50 / >50°). Under 30° is unshaded | `static/js/slope_overlay_core.js`; `installSlopeLayer` in `static/js/map.js`; `SLOPE_TILE_URL` in `config/settings/base.py` |
| Coverage rectangle | The slope raster's declared extent, `5.140242, 45.398181` → `11.47757, 48.230651`. Used as the source's `bounds` and to disable the layers-menu row outside it, because unshaded ground *outside* it means "not surveyed" rather than "under 30°" | `COVERAGE_BOUNDS` / `coversPoint` in `static/js/slope_overlay_core.js` |

## Accounts and tracking

| Term | Meaning | Code |
|------|---------|------|
| Account | The single public-user identity (`OneToOne` to `auth.User`, SNOW-514 collapsed the former `Subscriber` model into this). Auto-created at every public entry point (sign-in, register). `is_verified` is the sole "email proven reachable" gate, set by every email-proving link. `AUTH_USER_MODEL` is Django's built-in `auth.User`; `Account` is a domain profile, not the user model | `apps/accounts/models.py` |
| Region pin | A `Favourite` whose subject is the `MicroRegion` itself — `region` set, `location`/`latitude`/`longitude`/`elevation` null, one per `(user, region)` by partial unique constraint (SNOW-802). What a `Subscription` row became: a bookmark on a region's bulletin, made with the star roundel in the map's ribbon header and listed in the region + date panel that header's chip opens (SNOW-814 — a pill inside that panel, and a row in the pins sheet, until then), never in `favourites.geojson` | `Favourite.is_region_pin`; `FavouriteQuerySet.region_pins()` / `.placed()`; `favourites:region_toggle`; `favourites:region_list` |
| Subscription | **Retired** (SNOW-802) — an (Account, MicroRegion) pair that was meant to drive bulletin emails and never did. Its rows are region pins now; SNOW-805 drops the table | `apps/accounts/models.py` until SNOW-805 |
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
