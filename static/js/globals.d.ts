/*
 * static/js/globals.d.ts — the `window.pwa*` module channel, declared so the
 * type checker knows it exists (SNOW-899).
 *
 * Snowdesk's browser code is classic scripts sharing one global lexical
 * scope, with `<script>` order as the dependency graph (see map_state.js's
 * header and docs/map-and-api.md). A module publishes its API by assigning
 * to `self.pwaSomething`, and consumers read it back from `window`. That is
 * the project's module system, and TypeScript has no way to infer it — every
 * publish would otherwise be `Property 'pwaX' does not exist on type
 * 'Window'`, thirteen times over, for code that is entirely correct.
 *
 * This file is that contract written down. It is the first place the channel
 * has been enumerated anywhere, which is worth something on its own: adding a
 * module here is now a deliberate step, and `bin/js-globals-lint` (which
 * fails on a READ of a global nothing assigns) has a companion that describes
 * what the assignments are.
 *
 * WHY THE TYPES ARE `any` — FOR NOW
 * ---------------------------------
 * `checkJs` is opt-in per file, and today the opted-in set is the pure
 * `*_core.js` modules. None of them consumes another's published global; they
 * are read by `map.js` and its surfaces, which are NOT yet checked. So a
 * precise type here would be checked against nothing, while `any` costs
 * nothing and keeps this file honest about how far the migration has got.
 *
 * Narrowing one of these is the job of the ticket that opts in its consumers.
 * When `map.js` is checked, `pwaChoroplethCore` being `any` stops being free
 * and starts being the thing to fix — and the error will land there, on the
 * consumer, which is where the value is.
 *
 * Do NOT widen this file to silence an error in an opted-in module. An `any`
 * here is a note that the channel is untyped; an `any` used to paper over a
 * real mistyping is how a type checker becomes decoration.
 */

interface Window {
  // ---- pure logic extracted from map.js, unit-tested without a map -------
  /** static/js/choropleth_core.js — the danger-rating paint routine. */
  pwaChoroplethCore: any;
  /** static/js/calendar_core.js — the date picker's month grid maths. */
  pwaCalendarCore: any;
  /** static/js/scrubber_core.js — season-scrubber position and snapping. */
  pwaScrubberCore: any;
  /** static/js/search_core.js — diacritic-insensitive region/resort match. */
  pwaSearchCore: any;
  /** static/js/layer_visibility_core.js — overlay visibility derivation. */
  pwaLayerVisibilityCore: any;
  /** static/js/hatch_core.js — the downloaded-areas hatch image. */
  pwaHatchCore: any;
  /** static/js/map_viewport_core.js — stored-camera restore and clamping. */
  pwaViewportCore: any;
  /** static/js/slope_overlay_core.js — swisstopo coverage predicate. */
  pwaSlopeOverlayCore: any;
  /** static/js/map_weather_core.js — WMO code to icon, cluster collapse. */
  pwaWeatherCore: any;
  /** static/js/elevation_profile_core.js — route elevation profile maths. */
  pwaElevationProfileCore: any;
  /** static/js/route_markers_core.js — start/end marker placement. */
  pwaRouteMarkersCore: any;
  /** static/js/trip_deeplink_core.js — ?trip= / ?trip_share= resolution. */
  pwaTripDeepLinkCore: any;
  /** static/js/basemap_download_core.js — tile-range and byte arithmetic. */
  pwaBasemapDownloadCore: any;

  // ---- the shared map state channel --------------------------------------
  /** static/js/map_state.js — the frozen accessor surface over map state. */
  snowdeskMapState: any;

  // ---- shell services the cores may reach ---------------------------------
  /** static/js/i18n_strings.js — reads a strings <template> back. */
  pwaStrings: any;
  /** static/js/telemetry.js — the first-party event buffer. */
  pwaTelemetry: any;
}

/**
 * The service worker and several modules publish via `self` rather than
 * `window`. In a classic script at top level the two are the same object, so
 * this alias keeps `self.pwaX = …` checking against the same declarations.
 */
declare const self: Window & typeof globalThis;
