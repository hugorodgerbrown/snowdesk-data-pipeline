---
name: architecture
description: Repo map — config, the seventeen apps under apps/, track/, templates, src, static; what each app owns
status: current
last-reviewed: 2026-11-03
---

# Architecture

What each directory and app owns. The rules that bind them are in
[`AGENTS.md`](../AGENTS.md).

```
config/          Django project settings (base + development/staging/
                 production/perf overlays — see "Conventions → Code")
apps/            Parent package for the seventeen Django apps (SNOW-557 — moved
                 here without changing any app label; see
                 docs/decisions/ for the why)
  core/          Shared abstractions (BaseModel), HTTP-layer middleware, the
                 monitor_query_counts command, and the command run register —
                 ``CommandRun`` plus ``RegisteredCommand``, the base class
                 every project command inherits (SNOW-1189)
  locations/     Location — the domain primitive every place reaches (SNOW-700):
                 a resort's village, mid-station and peak, a favourite, an
                 observation, a region centroid. A curated place is simply a
                 Location with a ``name``, so Mont Fort is one row that four
                 resorts share. Plus ``ResortLocation``, the through model
                 carrying the role each location plays for a resort. Its own
                 app because regions, favourites and observations all
                 reference it (see docs/decisions/)
  regions/       Geographic reference data — MicroRegion / MajorRegion /
                 SubRegion / Resort, plus the fixture-maintenance commands
                 (dump_resorts_sheet, refresh_eaws_fixtures) and the
                 curated resort sheet + its import_resorts command. A
                 resort is retired, never deleted (SNOW-1404):
                 Resort.objects is the live set, Resort.all_objects
                 the whole table and the base manager
  ingest/        The providers' fetch and normalisation code, built to be
                 rehostable (SNOW-1247): it imports NO other apps.* package,
                 and everything else imports it only through
                 apps/ingest/public.py — tests/ingest/test_boundary.py fails
                 the build otherwise. normalise/ holds the pure functions
                 (SLF shape detection, the Météo-France translator, Open-Meteo
                 parsing, and since SNOW-1270 the render model builder, its
                 prose parser, the period transition and the day-character
                 cascade — store_bulletin builds each bulletin's model onto
                 SourceBulletin and the feed serves it — plus the day-rating
                 reading, the target day and the problem-terrain rules;
                 SourceDayRating, rated from the service's own bulletins,
                 is served at /api/data/v2/day-ratings/ and copied over
                 RegionDayRating by apply_day_rating_item);
                 sources/ the HTTP (the three bulletin providers,
                 Open-Meteo forecast + elevation, what3words), settings read
                 only via conf.py; runs.py walks a provider and yields
                 NormalisedBulletin / FetchOutcome — the app decides what to
                 write (SNOW-1249). SourceBulletin holds what each provider
                 published; the bulletins data feed reads it and every
                 Bulletin is applied from it (SNOW-1250). The feed serves
                 the render model and identity fields, never the CAAML,
                 and a Bulletin keeps none (SNOW-1337): raw surfaces read
                 it with source_caaml. SourceLocation
                 (the reference places, keyed by the uuid each instance
                 holds as Location.source_uuid) and SourceWeather (their
                 days) are the weather service: fetch_weather mirrors the
                 curated estate in, fetches it and applies each row into
                 Weather; /api/data/v2/weather/ serves it (SNOW-1253).
                 SourcePass and SourceResort hold the passes and resorts
                 as documents; mirror_reference_data copies production's
                 estate in hourly and /api/data/v2/{locations,passes,
                 resorts}/ serve it (SNOW-1271). SourceSkiSegment and
                 SourceSkiTour are swisstopo's ski-touring network and
                 SAC's tours, imported weekly by import_ski_tours and
                 served at /api/data/v2/ski-{network,tours}/ (SNOW-1276).
                 SourceRestZone holds FOEN's wildlife rest zones,
                 imported weekly by import_rest_zones and served at
                 /api/data/v2/rest-zones/ (SNOW-1263).
                 Read
                 docs/decisions/ingestion-is-a-rehostable-unit.md before
                 importing from it
  bulletins/     Everything that originates from the three CAAML bulletin
                 providers — the models (Bulletin, RegionBulletin,
                 PipelineRun, RegionDayRating, …), the apply step that
                 writes them from ingest's SourceBulletin and
                 SourceDayRating (services/apply.py, day_rating.py), the
                 per-provider fetch commands' glue (the HTTP and
                 translators are ingest's), the render model's
                 presentation (services/render_model.py: labels and copy;
                 the builder is ingest's), the ingestion commands (see
                 docs/management-commands.md), and their admin classes
  weather/       The Open-Meteo domain, rebuilt by SNOW-759 — one model,
                 ``Weather``: what was known about one Location on one day,
                 one row per (location, observed_on), never rewritten once
                 that day is past. Plus ``services/upsert.py`` (the write
                 rule), ``services/fetch.py`` (one coordinate's fetch, for
                 a single location), ``services/reference.py`` (mirror the
                 curated estate into ingest's station list and apply its
                 SourceWeather rows, SNOW-1253) and the ``fetch_weather``
                 command, scheduled 4×/day. The backfill (SNOW-731) fills
                 *past* days from the historical forecast API — never
                 today — and since SNOW-1275 is the weather service's
                 (apps/ingest/backfill.py, writing SourceWeather);
                 ``services/backfill.py`` applies each row into Weather
                 behind both the ``backfill_weather`` command (an hourly
                 budgeted sweep since SNOW-1245) and the LocationAdmin
                 action. Migrations 0001–0006 belong to
                 the estate SNOW-762 stripped and are retained because six
                 migrations in four other apps depend on them; 0007 creates
                 the new table; 0009 (SNOW-1337) adds forecast_on and
                 drops the forecast[] list — each fetch writes seven
                 rows, today to six days ahead, each with its hourly.
                 Read
                 docs/decisions/weather-is-one-immutable-location-row.md
                 before changing the shape of a row
  accounts/      Signed-token account flow and the one account page,
                 /account/settings/ (see docs/accounts.md); owns the
                 ``Account`` profile model (OneToOne to auth.User, not
                 AUTH_USER_MODEL itself — SNOW-514 collapsed the former
                 ``Subscriber`` into it), ``PushSubscription`` (FK to
                 ``Account``) and ``PasskeyCredential`` (FK to auth.User,
                 so any authenticated user can register a passkey without
                 an Account profile). ``Subscription`` is gone — its rows
                 became region pins (SNOW-802) and SNOW-805 dropped the
                 table
  favourites/    The one saved-place model — ``Favourite`` is a map pin, a
                 saved resort, or a region pin (a region with no
                 coordinate, SNOW-802) — plus its relevance scoring and the
                 HTMX partials under /favourites/. The kinds are listed
                 on two surfaces: dropped pins in the map's pins sheet
                 (``favourites:list``, ``dropped_pins()`` only), and all
                 three in search — dropped pins (SNOW-1264, "Pinned
                 places", matched by name), regions (SNOW-814,
                 ``favourites:region_list``) and resorts (SNOW-1229),
                 the pins and resorts read from favourites.geojson. A
                 pinned resort is drawn as a blue resort dot, not a star.
                 Region pins and resorts also make the Today panel behind
                 the date row's sun (SNOW-1366, ``favourites:today``,
                 today.py): today's danger per region, its resorts nested
                 with their weather, alphabetical
  observations/  Community field reports — the ``FieldObservation`` model and
                 the /partials/report/ submission endpoints
  routes/        Uploaded GPX routes (SNOW-685/686/690) — the ``Route`` model
                 (FK to auth.User), surfaced in the map's routes panel, plus
                 ``RouteShare``, the tokenised link whose claim COPIES a
                 route to another account (SNOW-764), and the glue that
                 runs track/'s terrain maths on a row (services/).
                 Also SkiSegment and SkiTour, the synced reference copy of
                 swisstopo's ski-touring network and SAC's tours
                 (SNOW-1276), served to draw mode by
                 /api/ski-network.geojson?bbox= (and ?overview=1 from
                 z9, SNOW-1322); a tour is found by its
                 summit and saved as a copy, source SKI_TOUR
                 (SNOW-1278, services/ski_tours.py). Every route stores
                 the avalanche regions its track crosses
                 (crossed_regions, SNOW-945), read by the Plan page,
                 the trip page and the trip alert
  trips/         Shareable trips (SNOW-819) — ``Trip`` (a route the organiser
                 owns, on a named day, at a stated time and meeting point)
                 and ``TripParticipant`` (every account that has SAVED it,
                 organiser included). ``/trips/`` is the one saved-thing
                 LIST page that survived SNOW-803, because a trip is
                 indexed by WHEN and the map indexes by where. The geometry
                 is a SNAPSHOT copied at creation and never re-read, so a
                 trip survives the source route being renamed or deleted.
                 SNOW-848 took the SOCIAL surface off it: no roster, no
                 count, no going state — someone plans a trip, sends the
                 link, and whoever opens it saves it, seeing neither who
                 else holds it nor how many do. The rows stay
                 (``for_user`` filters on them) and the organiser is still
                 named; read
                 docs/decisions/a-trip-is-one-object-with-a-roster.md,
                 whose disclosure-rule bullet SNOW-848 supersedes, before
                 putting anything about the other savers back on a page.
                 A trip copies its route's crossed_regions; its Plan
                 tiles and danger lines show every crossed region, and
                 the alert treats it as one place (SNOW-945)
  downloads/     Offline basemap areas synced to an account (SNOW-749) — the
                 ``DownloadArea`` model (FK to auth.User) and its sync API.
                 The DEFINITION of an area only: the tiles stay in each
                 device's own pinned Cache Storage bucket and are never
                 uploaded, so a row carries no byte count and no tile indices
  analytics/     fivebar goals and the /api/telemetry receiver (see
                 docs/telemetry-pipeline.md). NOTE: signals.py here means
                 *telemetry* signals, not django.db.models.signals — it does
                 not contradict the no-signals-for-side-effects rule below
  mcp_server/    JSON-RPC MCP tools at POST /api/mcp/ (docs/mcp-server.md);
                 no models of its own — it reads the bulletins/regions tables.
                 Every call needs an mcp-auth bearer token (SNOW-1193).
                 Both servers carry the docs tools (SNOW-1393,
                 docs_tools.py / docs_corpus.py): the public one over
                 the help topics, bulletin guide and explainers, ops
                 over every doc, decision and CLAUDE.md.
                 ops_tools.py is the staff ops catalogue, served at the
                 sibling POST /api/ops/mcp/ (SNOW-1191) — never nest it
                 under /api/mcp/: token audience is a URL prefix.
                 resort_tools.py (SNOW-1401) lets staff read and
                 correct resorts from a chat — get / find / update /
                 create / retire / restore, each write a preview then
                 an audited commit
  oauth/         Snowdesk's side of the MCP OAuth server (docs/oauth.md).
                 The server is mcp-auth on django-oauth-toolkit (SNOW-1193),
                 mounted from config/urls.py; this app holds the connect
                 rule (policy.verified_account), the verify-first gate in
                 front of /oauth/authorize/, the consent and returning
                 templates, the settings-page disconnect and the token
                 purge. No models — read
                 docs/decisions/mcp-auth-replaces-the-hand-written-oauth-server.md
                 before changing the flow
  data_feed/     Production's read-only data feed for other services
                 (SNOW-1197) — GET /api/data/v1/{bulletins,weather,
                 locations,resorts}/, OAuth client credentials issued only
                 to a hand-made service client — and its clients:
                 pull_production_data (v1: --type, --since, --commit), which
                 replays bulletins through upsert_bulletin, and
                 pull_reference_data (SNOW-1272), which seeds an instance
                 from v2 — the estate whole by source_uuid with its
                 tombstones, then --recent N or --golden-week days of
                 bulletins, day ratings and weather. No models; never
                 reads a user-owned table (docs/data-feed.md)
  public/        Public-facing bulletin site (route map: docs/site-structure.md)
                 Splitting by surface (docs/decisions/public-splits-by-surface.md):
                 the component library is moving to design_system/ (SNOW-1427)
                 and the map page, its JSON API and sheets to map/ (SNOW-1428).
                 urls.py and api_urls.py stay here as the routing hub
    api.py       Plain JsonResponse endpoints — the map's feeds, plus
                 share-create, /api/version + sw-config, the bulletin
                 JSON alternates, weather detail and the superuser edit
                 endpoints
    api_urls.py  URL routing for /api/ (namespace: api:)
    debug_views.py Staff-only design-debug pages (mounted unconditionally, staff-gated — not a DEBUG gate)
track/           Pure track maths (SNOW-1316), root-level: imports no Django
                 and no apps.* (tests/track/test_boundary.py). The geo
                 helpers (track.geo), the terrain reader (terrain,
                 terrain_grid — load_grid takes the tile base URL), the
                 walk (slope_samples), legs, bank, fall line, passages,
                 the render builder (render: build_track_render, its fp)
                 and SAC tour ordering (sac). An instance draws an upload
                 with it; the reference-data service draws each SAC tour
                 with it (apps/ingest/ski_render.py, render_ski_tours)
templates/       Project-level templates shared across apps
  includes/      Reusable partials (bulletin_header.html, nav.html, …)
src/             Tailwind CSS source (main.css — not served directly)
static/          CSS/JS assets (includes compiled output.css)
logs/            Log files (gitignored except .gitkeep)
```

The `apps/bulletins/` ↔ `apps/regions/` split is deliberate — rationale in
[`docs/decisions/bulletins-regions-split.md`](decisions/bulletins-regions-split.md).
