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
  /** static/js/day_nav_core.js — the date row's day steps and empty-day lookup (SNOW-1195). */
  pwaDayNavCore: any;
  pwaStaleDayCore: any;
  /** static/js/sheet_playback_core.js — an object sheet pauses while the timeline plays (SNOW-1239). */
  pwaSheetPlayback: {
    createGuard(): {
      begin(): { signal: AbortSignal | undefined; isCurrent(): boolean };
      invalidate(): void;
    };
    shouldReload(source: unknown, playing: boolean): boolean;
    isPlaying(): boolean;
    isAbort(err: unknown): boolean;
    markPaused(sheetEl: HTMLElement | null, on: boolean): void;
  };
  /** static/js/today_core.js — whether the map's "Today" control has anywhere to go (SNOW-1165). */
  pwaTodayCore: {
    canGoToToday(date: string | null | undefined, today: string | null | undefined): boolean;
  };
  /** static/js/cached_stamp_core.js — the cached-copy stamp and download freshness (SNOW-1139). */
  pwaCachedStampCore: {
    isBeforeToday(value: unknown, now?: Date): boolean;
    formatDay(value: unknown, options?: { locale?: string }): string;
    formatStamp(value: unknown, options?: { now?: Date; locale?: string }): string;
  };
  /** static/js/offline_ready_core.js — "Get ready for offline": drain, then every area in turn (SNOW-1141). */
  pwaOfflineReadyCore: {
    DEFAULT_DRAIN_TIMEOUT_MS: number;
    run(deps: {
      drain(): unknown;
      waiting(): Promise<number> | number;
      areas: string[];
      syncOne(id: string): Promise<'ready' | 'partial' | 'absent'> | 'ready' | 'partial' | 'absent';
      onStep?(step: any): void;
      drainTimeoutMs?: number;
      wait?(ms: number): Promise<void>;
    }): Promise<{
      total: number;
      ready: number;
      waiting: number;
      outcomes: Array<{ id: string; outcome: 'ready' | 'partial' | 'absent' }>;
    }>;
    areaOutcome(result: { tiles?: string; content?: boolean } | null | undefined): 'ready' | 'partial' | 'absent';
    waitingCount(rows: unknown): number;
    describe(
      result: { total: number; ready: number; waiting: number },
      strings: Record<string, string>,
    ): { summary: string; queue: string };
    progressText(step: any, strings: Record<string, string>): string;
  };
  /** static/js/downloads_restore_queue_core.js — the "Download all" queue's order and stop rules (SNOW-1140). */
  pwaDownloadsRestoreQueueCore: {
    DEFAULT_TIMEOUT_MS: number;
    DEFAULT_POLL_MS: number;
    waitForIdle(deps: any, options?: { timeoutMs?: number; pollMs?: number }): Promise<boolean>;
    runQueue(
      items: Array<{ areaId: string }>,
      deps: any,
      options?: { timeoutMs?: number; pollMs?: number },
    ): Promise<{ started: number; skipped: number; stopped: string | null; silent: boolean; item?: any }>;
  };
  /** static/js/scrubber_core.js — season-scrubber position and snapping. */
  pwaScrubberCore: any;
  /** static/js/search_core.js — diacritic-insensitive region/resort match. */
  pwaSearchCore: any;
  /** static/js/layer_visibility_core.js — overlay visibility derivation. */
  pwaLayerVisibilityCore: any;
  /** static/js/sheet_detents_core.js — where a phone map sheet settles (SNOW-1097). */
  pwaSheetDetentsCore: any;
  /** static/js/map_region_panel.js — the loaded pinned-region ids search lists when empty. */
  pwaPinnedRegions: { ids(): string[]; ready(): Promise<void> };
  /**
   * static/js/map_overlay_exclusivity.js — the map's one-overlay-at-a-time
   * registry. Read by map_help_mode.js (SNOW-1119), which closes them all.
   */
  pwaMapOverlays?: {
    register(name: string, handlers: { close(): void; isOpen?(): boolean }): void;
    opening(name: string): void;
    closeAll(): void;
    openNames(): string[];
    names(): string[];
  };
  /** static/js/hatch_core.js — the downloaded-areas hatch image. */
  pwaHatchCore: any;
  /** static/js/map_viewport_core.js — stored-camera restore and clamping. */
  pwaViewportCore: any;
  /** static/js/slope_classes_core.js — the 30/35/40/45/50+ slope-class ramp. */
  pwaSlopeClassesCore: any;
  /** static/js/terrain_filter_core.js — class-tile decode and filter paint. */
  pwaTerrainFilterCore: any;
  /** static/js/bulletin_terrain_core.js — the problem-terrain layer's rules, states and clipping (SNOW-979, SNOW-1194). */
  pwaBulletinTerrainCore: any;
  /** static/js/map_weather_core.js — WMO code to icon, cluster collapse. */
  pwaWeatherCore: any;
  /** map.js — the weather payload's forecast window, for map_weather_window.js (SNOW-1196). */
  pwaWeatherWindow: any;
  /** static/js/map_day_notes.js — the notes about the day on the map, behind the date row's bell (SNOW-1206). */
  pwaDayNotes: {
    set(
      id: string,
      note: {
        title: string;
        detail: string;
        action?: { label: string; run: () => void };
        rank: number;
      } | null
    ): void;
  };
  /** static/js/elevation_profile_core.js — route elevation profile maths. */
  pwaElevationProfileCore: any;
  /** static/js/route_markers_core.js — start/end marker placement. */
  pwaRouteMarkersCore: any;
  /** static/js/route_slope_core.js — slope class buckets and segment split. */
  pwaRouteSlopeCore: any;
  /** static/js/route_legs_core.js — a saved route's leg lines and transitions. */
  pwaRouteLegsCore: any;
  /** static/js/route_drawing_core.js — the route layers every map draws. */
  pwaRouteDrawingCore: any;
  /** static/js/meeting_place_core.js — a meeting point placed against its route. */
  pwaMeetingPlaceCore: any;
  /** static/js/route_profile.js — the route profile, on the map and the trip pages. */
  pwaRouteProfile: any;
  /** static/js/trip_form.js — the Plan form's day tiles and read-back. */
  pwaTripForm: any;
  /** static/js/trip_route_profile.js — the trip pages' profile and its scrub. */
  pwaTripRouteProfile: any;
  /** static/js/example_guide_core.js — the example route walkthrough's steps and tests (SNOW-1230). */
  pwaExampleGuideCore: any;
  pwaResortGuideCore: any;
  /** static/js/map.js — opens a pending share's route by its token (SNOW-1230). */
  pwaRouteOpener: any;
  /** static/js/route_cursor_core.js — the cursor shared by the map and rails. */
  pwaRouteCursorCore: any;
  /** static/js/route_cursor_map_core.js — the route cursor placed on the map. */
  pwaRouteCursorMapCore: any;
  /** static/js/day_strips_core.js — the block under a point on the day strips. */
  pwaDayStripsCore: any;
  /** static/js/route_leader_core.js — the leader line's path. */
  pwaRouteLeaderCore: any;
  /** SNOW-1256: route_draw_core.js — the pure half of drawing a route. */
  pwaRouteDrawCore: any;
  /** SNOW-1256: route_draw.js — whether the map is in route draw mode. */
  pwaRouteDraw: any;
  /** map.js: swisstopo's ski-touring routes layer — hold(held), isEligible(). */
  pwaSkiRoutes: any;
  /** static/js/route_rail_core.js — rail one's ticks, figures and leg fills. */
  pwaRouteRailCore: any;
  /** static/js/aspect_wheel_core.js — the aspect wheel's state and SVG. */
  pwaAspectWheelCore: any;
  /** static/js/route_point_card_core.js — the point card's words and gradient. */
  pwaRoutePointCardCore: any;
  /** static/js/trip_deeplink_core.js — ?trip= / ?trip_share= resolution. */
  pwaTripDeepLinkCore: any;
  /** static/js/basemap_download_core.js — tile-range and byte arithmetic. */
  pwaBasemapDownloadCore: any;
  /** static/js/offline_audit_core.js — the offline-content report model. */
  pwaOfflineAuditCore: any;
  /** static/js/zoom_control_core.js — the zoom scale, ticks and bounds. */
  pwaZoomControlCore: any;

  // ---- the shared map state channel --------------------------------------
  /** static/js/map_state.js — the frozen accessor surface over map state. */
  snowdeskMapState: any;

  // ---- shell services the cores may reach ---------------------------------
  /** static/js/i18n_strings.js — reads a strings <template> back. */
  pwaStrings: any;
  /** static/js/telemetry.js — the first-party event buffer. */
  pwaTelemetry: any;
  /** static/js/offline_audit.js — the collector and DOM half of the report. */
  pwaOfflineAudit: any;
  /** static/js/sw_register.js — asks the worker to warm a URL list. */
  pwaWarmCache: any;
  /** static/js/db.js — the IndexedDB layer (schema version, upgrades). */
  pwaDb: any;
  /** static/js/pwa_network_mode.js — reads and sets the network mode. */
  pwaNetworkMode: any;
  /** static/js/map_weather_detail.js — the weather sheet; open/close/point. */
  pwaWeatherDetail: any;
  /** static/js/map_sheet.js — the sheet controller; attach/bindDetents/handOff/toast. */
  MapSheet: any;
  /** static/js/route_rail.js — rail one below the map; open/close/cursor. */
  pwaRouteRail: any;
  /** static/js/route_point_card.js — the point card on the map; attach/detach. */
  pwaRoutePointCard: any;
  /** static/js/route_leader.js — the leader line from the map to the rails. */
  pwaRouteLeader: any;
  /** static/js/map.js — the route cursor's screen point on the map. */
  pwaRouteCursorMap: any;
  /** static/js/map.js — the terrain filter's state: show/hide, get/setFilter. */
  pwaTerrainFilter: any;
  /** static/js/map.js — the region sheet's problem-terrain switch (SNOW-979, SNOW-1194): isEnabled, setEnabled(on, regionId?), availability. */
  pwaBulletinTerrain: any;
  /** static/js/terrain_filter_sheet.js — opens the terrain filter sheet. */
  pwaTerrainFilterSheet: any;
  /** static/js/map.js — the zoom-gated layers: list() → [{key, zoom, active}]. */
  pwaZoomGates: any;
  /** static/js/map.js — the painted day's level for a region: forRegion(id) → "low" … "very_high" | null. */
  pwaRegionLevel: any;
  /** static/js/map_region_sheet.js — reopen() → whether the sheet reopened for the region still selected. */
  pwaRegionSheet: any;
}

/**
 * The service worker and several modules publish via `self` rather than
 * `window`. In a classic script at top level the two are the same object, so
 * this alias keeps `self.pwaX = …` checking against the same declarations.
 */
declare const self: Window & typeof globalThis;
