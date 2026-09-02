---
name: map-and-api
description: / (public:home) MapLibre choropleth, scrubber, overlays, /api/ endpoints (ratings, geojson, summary, groupings, weather), routes.geojson
status: current
last-reviewed: 2026-08-19
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

Modules *outside* this set (`map_layer_sync_status.js`, `favourites.js`,
`routes.js`) must reach the shared state through `window.snowdeskMapState`
instead — and the surface modules among them are loaded from their own
surface partials rather than from the bundle's contiguous run, so they are
not members of `MAP_BUNDLE`. (`routes.js` reads no map state at all today:
nothing the routes panel does touches the map until SNOW-687 adds the
layer.) The distinction is not stylistic: a top-level `let` lands in the global lexical
scope but **not** on `window`, which is how `map_layer_sync_status.js` read
`window.MAP` for its entire life and always got `undefined`. See
`map_state.js`'s header.

`tests/js/_load_map_bundle.js` models the same order for Vitest, which
would otherwise evaluate each file as an ES module and module-scope every
one of those shared bindings.

### The map's date picker (SNOW-792)

`map_calendar.js` is the tenth surface, loading after `map_scrubber.js`. It
renders a month grid so a visitor can name a day instead of hitting it with
a drag — the scrubber track spans a seven-month season in a few hundred
pixels, so a pixel is about a day and a release snaps to whichever rated day
is nearest.

**The button and the panel are in the same place** — SNOW-794 moved both.

The tap target is the map's date control: one pill in the bottom-left stack
that both SAYS the day and OPENS the picker. It used to be two things a
screen apart — `#map-date-ribbon` in the bottom-left row said the date, and
`#map-calendar-toggle`, a transport button in the scrubber's row, changed
it. Both ids survive, nested, so `map.js` still writes the date into the
readout and `map_calendar.js` still binds the toggle; only the boxes moved.

The panel followed its trigger. It was a `position: fixed` sheet in the
bottom-RIGHT slot, sharing geometry with the favourites, observations and
routes sheets on the reasoning that every panel over the map should appear
in one place. Once the trigger moved left, that rule cost more than it paid:
a panel opening on the far side of the map from the button that opened it.

It is now an absolutely-positioned card inside `#map-legend`, opening into
the crook of the corner that stack and its bottom row make — clear of the
roundel column to its left and the date row beneath it, on the same two
offsets `#map-legend-card` uses. It is no longer handed to
`window.pwaOverlayBounds.positionSheet`: there is no floor to measure when
the anchor is the row's own box, and the anchor moves with the row. The
bottom-right sheets still use that module.

**What `positionSheet` measures changed with it.** The floor was
`#season-scrubber`, back when that was a full-width bar across the foot. The
scrubber now shares the bottom row rather than owning a bar, and is absent
entirely off-season and below 640px — where its rect reads all zeros and put
the floor at `-8`, sending every sheet on the map to the top of the
viewport. It measures `#map-date-row` instead: always present, always
lowest.

**It owns no commit path.** Picking a day dispatches `snowdesk:scrub-to`
`{date, source}`; `map_scrubber.js` listens and passes it to `commitDate` —
the same function a drag release calls, so the repaint, the `?d=` write and
the outgoing `snowdesk:date-changed` are identical either way. Two surfaces
writing the URL is two places for the URL and the paint to drift apart.

**The boot guard is the calendar's rule, not the scrubber's** (SNOW-794).
`map_scrubber.js` used to refuse a `?d=` outside the season on boot and on
back-navigation. The ratings payload is the whole archive, so the picker
pages back years — and a day it offered was committed on the click, then
refused on reload, leaving the map uncoloured while the URL and the grid
both named it. `isSelectableDate` replaced `isInSeason`: parseable, and not
in the future, which is exactly what `buildMonthGrid`'s `max` enforces.

That event name is a revival. It was the season ribbon's click-to-scrub
contract until SNOW-615 retired it: the ribbon cells stopped carrying click
handlers when they moved into the scrubber track, leaving `map.js` with a
listener no one dispatched to.

**What it offers is not the season.** Every day from the earliest the site
holds data for up to *today*; the future is the only hard stop, because
nothing on the map can answer for a day that has not happened. The season is
a highlight inside that range, not a fence around it.

That distinction is the second thing this ticket got wrong and fixed. The
first draft made the season the selectable range, which stopped being right
the moment the map grew a weather layer: weather has an answer for a day in
September, and the picker could not reach it at all.

SNOW-794 finished the thought. An out-of-season date used to park the thumb
at whichever end of the track it was past — called "the honest reading of
the season ran out" at the time, and it was not: the URL said July and the
thumb said April. The scrubber is now simply **not shown** for such a day
(`data-in-season`, server-rendered and kept current by
`static/js/map_scrubber_reveal.js`), so the calendar is its reveal. A
control that cannot answer is better absent than wrong.

**The grid carries the danger colour.** `buildMonthGrid` takes a
`ratings` map — date key to EAWS int for the focused region — and each day
cell is filled from it, in the saturated palette with the `--color-eaws-*-fg`
ink the bulletin page's own season calendar uses. That is the fact the
scrubber ribbon paints into its track, and moving it here is what lets the
scrubber be dropped entirely below 640px.

**The season tint is the only per-day mark.** A dot on the days actually
carrying a bulletin was built and removed: the thing it distinguished — a
gap in the archive inside the season — is an operator's concern, not a
visitor's, and a visitor who lands on such a day already sees an uncoloured
map. If you want to see where the archive is thin, query
`RegionDayRating`, not the picker.

The paging floor comes from the ratings cache rather than the server: asking
the DB for its earliest row would be a query on the home page's critical
path, and that page has a query-count budget ([`query-counts.md`](query-counts.md)).

The grid is built client-side (`calendar_core.js` holds the maths,
unit-tested in `tests/js/test_calendar_core.js`), so every word in it is
handed over by the server: `_base_map_context` supplies Django's own lazy
month and weekday tables, and the popup partial carries them as a
pipe-separated `data-months` attribute plus a rendered weekday row. Nothing
user-facing is a JavaScript literal — see [`i18n.md`](i18n.md).



**The order is enforced, not hand-maintained** (SNOW-647).
`tests/public/test_map_script_order.py` renders the homepage and asserts its
script sequence matches `MAP_BUNDLE`, that the bundle loads as one
uninterrupted run, and that declarations precede `map.js` while surfaces
follow it. Adding a map module therefore means editing two places — the
template and `MAP_BUNDLE` — and forgetting either is a failing test rather
than a map that stops booting. `map.js`'s header list is prose and is
deliberately not asserted.

### One open overlay at a time (SNOW-658)

Ten surfaces float over the map, and only one is ever meaningful at once:
the layers menu, the four UGC panels (downloads, favourites, field
observations, and routes since SNOW-686), the anchored detail popup a resort
pin, a favourite pin or (since SNOW-687) a saved route's line opens, the
legend card (`#map-legend-card`), the help
tour's coachmark (`#map-help-overlay`), the bulletin fill-strength flyout
(`#map-fill-flyout`) and the date picker (`map-calendar`, SNOW-792). Each registers with `window.pwaMapOverlays`
(`static/js/map_overlay_exclusivity.js`) — a name plus `isOpen()` and
`close()` — and calls `opening(name)` before it reveals itself; the
registry closes the rest. `MapSheet.attach` registers on a caller's behalf,
so the two sheets that go through it need nothing of their own.

It replaces the pairwise wiring the surfaces carried until then
(`window.pwaLayersMenu.close()` from the downloads sheet;
`snowdesk:map-detail-opening` / `snowdesk:favourite-detail-close` between
the popup and the favourites sheet) — three of the fifteen relationships
between six surfaces, each remembered by whichever ticket added the newest
one, with the report sheet in none of them. A surface added later gets the
whole matrix by registering, without naming a sibling;
`tests/js/test_map_overlay_exclusivity_surfaces.js` runs that matrix, so
one that forgets to register fails there.

Each panel's own roundel toggles: a second tap closes what the first
opened, matching the layers pill. The one exception is the help "?"
roundel, which always re-opens the tour from step 1 — the deliberate "show
me again" affordance it has had since SNOW-457.

The legend, the coachmark and the fill flyout joined last, and none was
covered by the outside-click dismiss it already had: their toggles call
`stopPropagation` (without it the opening click reads as a click away), so
no other surface's document handler ever saw them open, and a surface
opened programmatically — the downloads panel from its roundel, the detail
popup from a MapLibre canvas event — reached no document handler at all.
The fill flyout's third dismiss path, the observer that closes it when the
collapsible strip hides the roundel it is anchored to, answers a different
question again and stays — and SNOW-664 gave the layers menu the same
observer, for the same reason: `#basemap-pill` moved into that strip, so a
menu left open beside a hidden roundel is now an orphan there too. The
coachmark closes
without persisting `snowdesk.map.help`: being displaced is not the user
saying "seen", which is what Done / Escape / "×" mean. Registering it is
safe because every tour step targets map chrome — a roundel, the readout,
the scrubber — and none targets a panel's contents, so nothing the tour
closes is a step's own target.

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
bottom of the map. The thumb rests at today's position within the Nov–May
window, and since SNOW-793 it **commits today** when the URL names no day:
a bare querystring means today, so the map opens coloured for it and
`#map-date-ribbon` names it. SNOW-660 had left that case uncommitted
because the default it removed was the season's *last populated* day — a
date the map derived and nothing on screen named. Today is neither, which
is why it is back and why it must never be swapped for the derived date
again. The uncoloured "No date selected" state survives as the fallback
for a page with no readable `data-today`. Precedence is `?d=` first, today
behind it, in `readDisplayDate()` (`static/js/map_shared.js`).

The boot default is **not** written to the URL: a bare link keeps meaning
"today" rather than pinning to the day it was copied. Every commit the
visitor makes still writes `?d=`, today included, so a chosen day survives
a reload — the scrubber on every commit, the timelapse only where playback
*settles* (a stop, and the skip-to-start/skip-to-end jumps), because Safari
throttles `history.replaceState` by throwing and five frames a second would
hit that limit inside half a minute. Both go through `writeUrlDateParam` in
`static/js/map_shared.js`. Dragging recolours every region from the
`/api/ratings/?country=ch` payload to show how danger evolved on the
selected date, and pressing the play button steps through the season as
a timelapse. The drawer (when open) follows the scrubber via the
`snowdesk:date-changed` event, fetching `/api/region/<id>/summary/?d=…`
so the bulletin shown matches the scrubbed-to date. The full-season
payload is fetched by the scrubber's own init — it needs the date range to
leave its loading state (`map_scrubber.js`, SNOW-234) — and cached for the
session via `getSeasonRatings()` in `static/js/map_shared.js`, so scrubs and
timelapse playback render from the in-memory cache. It is **not** lazy until
first interaction; this paragraph said so until SNOW-793 measured it. The
SNOW-793 default deliberately does not wait on that promise: its output is
the thumb and the date announcement, and map.js's separate single-date boot
leg paints the choropleth, so a slow or failed season fetch must not hold
the ribbon at "No date selected" over an already-coloured map.

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
{uuid, name, created_at, container}` — map.js passes an empty
`[data-favourite-detail]` container that `static/js/favourites.js` fills,
then map.js anchors it in a MapLibre popup at the pin (SNOW-499: an existing
favourite is a point fixed to the map, so its detail is a *pinned popup*, not
the docked create/placement sheet — the sheet stays put only while the map
pans a mobile pin under it during placement).

SNOW-658: that popup holds the pin's name, the time it was saved as a muted
relative subheader, and the close — the same shape as the observation pin's
popup, and it shares the one `formatRelativeTime` in `map_shared.js`
(published for non-bundle modules as `window.snowdeskMapFormat.relativeTime`).
`created_at` rides on the feature rather than behind a per-pin fetch so the
popup opens offline. Renaming and removing a favourite live in the favourites
panel, so the popup no longer carries the `__UUID__`-templated rename/delete
forms it once reconstructed client-side, nor the `snowdesk:favourite-detail-close`
event a delete used to dispatch. `favourite_rename_url_template` /
`favourite_delete_url_template` remain in the page context — the panel's
inline rename and the resort-popup star still post to them. The "Add
favourite" flow itself (placement, drag-to-refine, the create sheet, CSRF
handling) is documented in `apps/favourites/views.py`'s module docstring and
`static/js/favourites.js`'s header comment.

**Routes panel (SNOW-686)**: the fourth UGC roundel (`#route-add-btn`,
`.map-utility-pill--route`) and the panel it opens (`#route-sheet`,
`public/partials/_routes_surface.html`), listing the signed-in user's
imported GPX routes with a file picker to add one and the shared
pencil/trash row controls. Built to the same shape as the favourites panel
and described in `static/js/routes.js`'s header; the differences worth
knowing here:

- **Open to every visitor since SNOW-724**, which retired the `routes`
  rollout flag the panel landed behind. The one gate left is
  `routes_eligible` (`_routes_context()` — authentication), which
  `routes.js` reads off `data-routes-eligible` to choose between the real
  list and an anonymous sign-in CTA; the roundel and the sheet themselves
  are always in the DOM.
- **No map layer, so no overlay switch and no roundel ring.** The panel
  includes `includes/_ugc_panel.html` *without* a `toggle_id`, which is why
  that parameter is optional: a switch wired to nothing is a worse lie than
  an absent one. The roundel likewise carries no `data-overlay-shown`.
- **Upload is online-only** and posts multipart straight to
  `routes:create` rather than through `window.pwaMutationQueue` — the queue
  persists JSON bodies in IndexedDB and a multipart file is a different
  shape, so replayable uploads are scope of their own. SNOW-504's resort
  toggle is the precedent. An offline tap is refused before the picker
  opens, with a line that says so.
- **Rows come from `routes:list` at `?variant=map`**
  (`ROUTE_LIST_MAP_VARIANT`), which selects
  `routes/partials/_route_list_map.html` and the shared
  `includes/_ugc_panel_row.html` row; the default variant keeps
  `_route.html`, the row `route_create` / `route_rename` return. The URL is
  used verbatim by the client — rebuilding it would drop the query string
  and render the wrong variant.
- **`route_rename` is unchanged from SNOW-685** and still answers with
  `_route.html`. The panel re-reads the list after a rename or an upload
  rather than swapping the response in, exactly as `favourites.js` does for
  the same reason: the returned row is deliberately a different shape.
- Row meta line is distance and ascent — and **ascent is omitted entirely
  when `ascent_m` is null**, since a GPX with no elevation data means "we
  don't know", not "flat".

**Slope-angle overlay (SNOW-691)**: the map's only `raster` source, and its
only layer whose data is fetched straight from a third party rather than
through a Snowdesk endpoint. The tiles are swisstopo's
`ch.swisstopo.hangneigung-ueber_30` WMTS, read from `SLOPE_TILE_URL`
(`config/settings/base.py`, env-overridable so SNOW-693 can move the origin
without a code change) and rendered onto `#map` as `data-slope-tile-url`
for an eligible request only — and "eligible" is simply
`bool(settings.SLOPE_TILE_URL)`. SNOW-724 retired the `slope_layer` flag
and moved that job onto the setting, which was already the operator's kill
switch by another name: clearing the env var withdraws the row, the raster
and the legend key on a restart rather than a deploy.

It differs from every other opt-in overlay in one way worth knowing before
you go looking for the missing code: **it is installed eagerly and hidden,
not lazy-loaded.** MapLibre requests no tiles for a source whose layers are
all `visibility: none`, so a hidden raster costs one source entry and no
network at all — which is why `slope` appears in `OVERLAY_LAYER_IDS` (both
copies) but not in the picker's lazy branch, and why there is no
`ensureOverlayLoaded` case and no `snowdesk:overlay-load` handling for it.
The generic `setLayoutProperty` path is the whole toggle.

Three source/paint properties are load-bearing rather than decorative.
`maxzoom` is **16**, not the service's advertised 17: z17 is a server-side
upscale, differing from a 2x-upscaled z16 in 1.3% of pixels against 2.7%
for z15→z16 and 7.0% for z14→z15, so capping costs nothing visible and
saves four requests per tile of screen at touring zooms. Declaring it at
all is SNOW-604's lesson — a raster source's `maxzoom` defaults to 22.
`bounds` is the declared coverage rectangle (`COVERAGE_BOUNDS` in
`static/js/slope_overlay_core.js`), which keeps MapLibre from requesting
the tiles outside it that the service answers with HTTP 400 and a JSON body
rather than an empty tile. `raster-resampling` is `nearest`, not MapLibre's
default `linear`: the raster is categorical, and interpolating between two
class colours invents a colour that corresponds to no slope class.

The blockiness that `nearest` leaves at high zoom is the source's own 10 m
grid, not a rendering fault — measured against the served tiles the
smallest distinguishable feature is ~10 m at every zoom (a 2px run at z14,
3px at z15, 6px at z16, each doubling with the zoom rather than resolving
further). Note for SNOW-693: Copernicus GLO-30 is **30 m**, so a
self-hosted replacement would be three times coarser than this inside the
area swisstopo covers.

`slope-raster` resolves `beforeId: 'regions-fill'` so it lands under the
choropleth from either install path (boot installs it first; the
`styledata` re-install runs the regions first).
`updateSlopeRowAvailability` (on `moveend`) disables the layers-menu row
with a reason once the viewport centre leaves the rectangle, via a
namespaced marker (`data-slope-disabled-out-of-coverage`). An earlier
build also drew the rectangle as a
`slope-coverage-line` layer; it was removed as a hard box across the whole
map that cost more legibility than it bought.

One non-obvious coupling: `installSlopeLayer` nulls `map.js`'s memoised
`attributionSourceIds` and re-runs `updateMapAttribution`.
That cache is populated at `style.load` and this source is added
afterwards, so without the reset swisstopo's attribution — a licence
obligation, not a nicety — never reaches the legend's "Map data" section.

The legend's "Slope angle" heading is an anchor into
`/help/#help-topic-slope` (`public/help/_topic_slope.html`, which renders
for every visitor — the help topic is not gated with the layer, so a
reader whose environment has no `SLOPE_TILE_URL` can still read what the
layer would have said). The id sits on a
wrapper `div` rather than on `_collapsible_panel.html`'s `<details>`, so
that shared partial keeps one shape for every caller. The source's
`attribution` names the DATASET rather than the publisher — two basemaps
are also swisstopo and contribute a bare `© swisstopo`, and
`updateMapAttribution` unions by exact string, so a matching value rendered
as "© swisstopo · swisstopo".

Offline caching: no `OVERLAY_RESOURCES` entry, so the row carries no sync
dot state and the offline gate never touches it. The tiles ride the service
worker's cross-origin basemap stale-while-revalidate cache instead — the
slope origin joins the `register-basemap-origins` payload alongside the
basemap origins — so terrain browsed online still paints offline. Pinning
slope tiles as part of a deliberate area download is SNOW-692.

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
re-lifts the favourite and community-observation layers to the top and is called at the end of **every** layer-install path
(`installRegionsLayers`, `installOverlayLayers`, `installResortsLayer`,
`installFavouritesLayer`, `installCommunityReportsLayer`,
`installBulletinGroupingsLayer`) — so any new
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
overlay layer driven by **opacity** rather than visibility, because it is the
map's hit-test target and `queryRenderedFeatures` returns nothing from a
layer at `visibility: none`. Its opacity is the user's chosen step (0 / 0.25 /
0.5 / 0.75 / 1, from the `#map-fill-toggle` flyout) AND-ed with any active
suppression; its visibility is derived from that plus the Micro regions row by
`window.pwaLayerVisibilityCore.regionsFillLayout`, and both are applied
through `applyBulletinsVisibility` in `map.js`, which is the single writer.
Placement focus is unaffected — it snapshots and restores whatever
`visibility` it finds.

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
| `GET /api/ratings/` | `api:ratings` | `{date_iso: {region_id: rating_int}}` — unified ratings endpoint (SNOW-239). Accepts optional `?d=YYYY-MM-DD` (restrict to one date) and `?country=ch\|fr\|at\|it` (restrict by country). A cold open uses `?d=<the day readDisplayDate() resolves>&country=ch` (~2 KB) — the `?d=` day, or today since SNOW-793 — and issues no single-date request at all only when neither is known (SNOW-660 — no day asked for, nothing to paint); the scrubber's init separately pulls `?country=ch` (full CH season, ~40 KB). Compact int encoding: `0=no_rating, 1=low, 2=moderate, 3=considerable, 4=high, 5=very_high`. Server-side `cache.get_or_set` keyed on `(country, date)` keeps DB hits to one per cache window (5 min for single-date, 1 h for full-season). |
| `GET /api/resorts-by-region/` | `api:resorts_by_region` | `{region_id: [resort_name, …]}` — alphabetical; regions without resorts omitted |
| `GET /api/resorts.geojson` | `api:resorts_geojson` | GeoJSON FeatureCollection of geocoded resorts (Points; `[lon, lat]` per RFC 7946); properties `id`, `name`, `region_id`, `needs_review`, `tier` (SNOW-543 — `CORE`/`STANDARD`/`MINOR`, the curated map-prominence verdict the pin radius is interpolated from) |
| `GET /api/regions.geojson` | `api:regions_geojson` | GeoJSON FeatureCollection from `Region.boundary` (L4 fixture regions); each feature has `properties.id` + `properties.name`, plus `properties.download` (SNOW-521 — see below) when the region has a precomputed offline-basemap size. |
| `GET /api/major-regions.geojson` | `api:major_regions_geojson` | GeoJSON FeatureCollection of L1 EAWS major regions (e.g. `CH-4`, `CH-5`) with `properties.id` + `properties.name`. Never carries `properties.download` — offline-basemap download is a MicroRegion-only feature (SNOW-521). |
| `GET /api/sub-regions.geojson` | `api:sub_regions_geojson` | GeoJSON FeatureCollection of L2 EAWS sub-regions (e.g. `CH-41`, `CH-42`) with `properties.id` + `properties.name`. Never carries `properties.download`, same as major-regions.geojson above. |
| `GET /api/region-basemap-tiles/?id=<region_id>` | `api:region_basemap_tiles` | The full precomputed `basemap_download` blob (incl. `z` tile-index ranges) for one MicroRegion (`MicroRegion.region_id` only — SNOW-521). 400 on a missing `?id=`; 404 for an unknown id or a region with no computed blob yet. Fetched on demand — see [`offline-map.md`](offline-map.md#offline-overlay-caches--download-basemap-snow-492-snow-521) for the full download flow. |
| `GET /api/region/<region_id>/summary/` | `api:region_summary` | `{html, level}` — `html` is the server-rendered MapLibre Popup snippet (danger-rating chip + geographic breadcrumb); `level` is the rating string the JS uses to stamp `data-level` on the popup container for the border colour. Honours `?d=YYYY-MM-DD` so the popup can show any scrubbed-to date; returns 400 on a malformed value. **Currently unused by the client** — the region popup lost its trigger (see the intro above); the endpoint is kept alongside `openRegionPopup` pending a decision on a replacement detail surface. |
| `GET /api/bulletin-groupings.geojson` | `api:bulletin_groupings_geojson` | `{"type":"FeatureCollection","features":[…]}` — a **single day's** dissolved bulletin boundaries. `?d=YYYY-MM-DD` is **required** (400 `date_required` if absent, 400 `malformed date` on a bad value). Each feature's geometry is the dissolved outer boundary of all L4 micro-regions sharing that bulletin; `properties` carries `bulletin_id`, `date`, and `countries` (sorted ISO-2 list). Accepts optional `?country=ch\|fr\|at\|it`; filters by membership in the `countries` list (a cross-border bulletin with `["AT","IT"]` appears for both `?country=at` and `?country=it`). Server-side `cache.get_or_set` keyed on `(country, date)` (5 min). **`Cache-Control` is date-aware (SNOW-526):** `apps.bulletins.services.settled.earliest_mutable_date()` derives a settled/unsettled threshold from the fetcher registry (`apps.bulletins.services.slf_fetcher.get_sources()`), memoised at the call site (`apps.public.api._cached_earliest_mutable_date()`, 60s) to avoid a per-`BulletinSource` DB query on every request; a settled `?d=` gets `public, max-age=604800, immutable`, otherwise `public, max-age=300` (unchanged). The `immutable` token is also `sw.js`'s signal to persist the response for offline use — see `docs/decisions/date-aware-cache-policy.md`. **Why single-date:** the endpoint previously returned the whole season keyed by date in one payload; once the historical backfill landed, serialising every day's dissolved geometry at once pushed the web worker past its 512 MB limit (SNOW-323 follow-up). The JS overlay ("Bulletin groupings" — the boundary now draws alongside the choropleth, SNOW-506; SNOW-521 removed the standalone `data-overlay-key="l3"` layers-menu row, and SNOW-656 moved what governs it from the `l4` row to the bulletin-fill step, since the boundary is a bulletin concept and, like the choropleth, is date-bound — at step 0 it is hidden and a scrubbed date does not refetch it) fetches one day at a time via `fetchBulletinGroupingsForDate(dateKey)` (no `?country=` filter, so cross-border rows are present), memoising each date for the session. It draws the boundary only once the scrubber **settles** (`GROUPINGS_SETTLE_MS = 250`), blanking the layer during active drag/playback so it neither thrashes the network nor lags a frame behind the choropleth. The MapLibre layer uses an array-membership filter (`['in', c, ['get','countries']]`) instead of the scalar `match` filter used by L1/L2, because `countries` is a JSON list not a string. |
| `GET /api/weather.geojson` | `api:weather_geojson` | `{"type":"FeatureCollection","features":[…]}` — the map's Weather overlay (SNOW-761). **One** Location-anchored feed replacing the resort-anchored `forecast-weather.geojson` and region-anchored `region-weather.geojson` SNOW-762 removed: a region centroid is a `Location` like any other now, so there is no tier to switch between and no zoom threshold to switch it at. One feature per `Location.objects.public()` row — **never `active()`**, which also reaches every location a `Favourite` points at; that is the billable set, not the visible one, and serving it here would put a stranger's private pin and its coordinates on a public map (`tests/public/test_weather_geojson_api.py` asserts the absence). `properties` carries `location_id`, `name`, `elevation_m` and `days` (`{iso_date: {code, tmax}}`) and **nothing else** — no `kind`, no `resort_id`, no `region_id`, because no layer reads them. `code` is the raw WMO weather interpretation code; the icon filename is derived client-side in `static/js/map_weather_core.js`, whose table is held to the Python one by `tests/weather/services/test_icon_table_parity.py`. `days` covers today and the week ahead from **one** `Weather` row per location (its `observed_on` plus its `forecast` column), so a scrubbed date re-projects in memory with no second request; a location with no row for today still gets a feature with an empty `days`, which the layer's `icon != ''` filter drops. Server-side `cache.get_or_set` keyed on the day (5 min). Publicly cacheable and listed in `_POSTHOG_EXEMPT_PATHS`, without which `Vary: Cookie` would defeat it. Freshness headers report the **oldest** `fetched_at` in the payload; `unsafe_after` is omitted, since weather is not safety-critical. Full contract: [`weather-surfaces.md`](weather-surfaces.md). |
| `GET /api/community-reports.geojson` | `api:community_reports_geojson` | `{"type":"FeatureCollection","features":[…]}` — anonymised, clustered "Community reports" overlay (SNOW-419). Covers `FieldObservation` rows from the last 48 hours. Each feature's `coordinates` are `[lon, lat]` rounded to 3 dp (~80–110 m); `properties` carries `type` (`OBSERVATION_TYPE` value), `type_label` (display label), `observed_at` (ISO, the instant as recorded — it was floored to the nearest 15 min until that floor was found to protect nothing and to make the map disagree with the reporter's own panel), and `region_name` (or `null`). Never serialises `latitude`/`longitude` at full precision, `gps_*`, `accuracy_radius_km`, `user`, or the row's pk. `Cache-Control: private, no-store` — unlike the other geojson endpoints it is **not** publicly cacheable and **not** in `_POSTHOG_EXEMPT_PATHS` (SNOW-459); public caching is tracked separately (SNOW-469). It carries a 120s client-side freshness window via `X-Data-Max-Age`. The JS overlay (`data-overlay-key="community_reports"`, default **off**) clusters the source client-side (`cluster: true`) and fades pins by age via a client-computed `_ageOpacity` feature property (no MapLibre "now" expression exists). |
| `GET /routes/routes.geojson` | `routes:geojson` | `{"type":"FeatureCollection","features":[…]}` — SNOW-687's routes line layer: one **LineString** feature per `Route` the **requesting user** owns. `coordinates` are `Route.points` verbatim — `[lon, lat, ele]` in GeoJSON axis order (RFC 7946), already simplified at ingest by SNOW-685, so there is no per-render transform and no axis swap can creep in between the stored and served shapes. `ele` is metres, or `null` for a point whose source `<ele>` was absent. `properties` carries `uuid`, `name`, `distance_m`, `ascent_m` and `bounds`. **`ascent_m` is served as stored, `null` included** — null means "the GPX carried no elevation data", NOT "flat", and the client omits the ascent line entirely rather than rendering a zero for an unknown (`Route`'s docstring is explicit that the substitution would be a safety-relevant lie). `bounds` is the flat `[min_lon, min_lat, max_lon, max_lat]` bbox, on the feature so a tap can fit the viewport from the payload the map already holds, offline included. **403** for anonymous callers; owner-scoped via `Route.objects.for_user()` in **one** query however many routes the user has. Not `@require_htmx` — a JS `fetch()` consumes it, not an HTMX swap. `Cache-Control: private, no-store`. Freshness headers follow `community-reports.geojson` rather than `favourites.geojson` (which carries none): `generated_at` is the newest route's `updated_at`, and **`unsafe_after` is omitted** — a user's own uploaded track is not safety-critical data, so the client's freshness state saturates at "stale" and never escalates to "unsafe". Note the path: mounted under `/routes/`, not `/api/`, alongside the panel's HTMX endpoints. **SNOW-764 widens it**: a request whose session holds a followed-but-unclaimed `RouteShare` token gets that share's route too, signed in or not, so the `/?route_share=<token>` deep link lands on a map that can draw what the link was for. A pending feature carries `token` and `pending: true` and **no `uuid`** — the rename and delete endpoints are addressed by uuid and owner-scoped, so a non-owner is never handed one — and `static/js/map.js` draws it through its own dashed `routes-line-pending` layer. The anonymous branch still 403s for a session with nothing pending, and both branches short-circuit on an empty session list before any query ([why](decisions/route-share-pending-claim-in-session.md)). |

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

The data flow on map load is: `map.js` fetches `regions.geojson?country=ch`
and the single-date ratings leg for `readDisplayDate()` — the `?d=` day, or
today (SNOW-793) — in parallel. The leg is skipped only when neither is
known, which needs a page with no readable `data-today`; nothing is painted
then, because the map has not been asked for a day and inventing one is the
bug SNOW-660 removed. "Inventing" means the season's last populated day
specifically: today is a default, not an invention, because
`#map-date-ribbon` names it. Once both resolve, it calls
`map.setFeatureState({source: 'regions', id: numericFeatureId}, {rating})` for
each region, so the choropleth fill layer reads exclusively from feature-state
(no property-based fallback). Regions with no bulletin today are absent from the
ratings response; their feature-state is left unset and the `match` expression
defaults to `no_rating`.

Because rating is feature-state, and `MAP.setStyle()` (the basemap picker)
wipes feature-state along with the source, **every basemap swap has to
repaint the whole choropleth** — otherwise every region falls through to
`no_rating` and the map goes grey. `map.js`'s `repaintAfterStyleSwap` does
that, taking the date from `currentDisplayedDate` in preference to
`readDisplayDate()` (the two can differ mid-session — a timelapse frame
commits a date without writing one). When neither is known — no committed
date, no `?d=`, no readable `data-today` — the swap repaints **nothing**:
the wiped feature-state is the correct, uncoloured map (SNOW-660).

The fill itself is painted **opaque**, with the translucency baked into the
colours by `compositeOverBackdrop()` in `static/js/choropleth_core.js` — a
translucent fill blends with the basemap, which made one rating render as a
different colour on each of the five basemap styles. See
[`decisions/choropleth-blended-not-translucent.md`](decisions/choropleth-blended-not-translucent.md).

The shared top-nav partial used on the map and other public pages is
documented separately in [`nav_implementation_spec.md`](nav_implementation_spec.md).

## Edit-resorts mode (SNOW-74) — superusers only

`/?edit=resorts` enters resort-edit mode when the request user is a
superuser (SNOW-86 gated this on an `edit_map` waffle flag seeded
`superusers=True`; SNOW-724 replaced the flag with the equivalent Django
check, same audience). The page renders a right-hand panel with a
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
| `/api/edit/resorts/queue/` | `api:edit_resorts_queue` | GET | Superuser-only. Returns `{all_resorts, sub_regions}`, ordered `region_id ASC, name ASC` so the panel can group rows by L2 area (e.g. `CH-41`). Each entry carries a `details` object holding every `RESORT_DETAIL_FIELDS` value, so selecting a row needs no second fetch. |
| `/api/edit/resorts/<int:resort_id>/save/` | `api:edit_resort_save` | POST | Superuser-only. JSON body `{latitude, longitude, details?}`; coordinates outside `_SWISS_BBOX` are hard-rejected with 400. `details` is optional and may be partial — an omitted key keeps its stored value. An invalid field returns `400 {"error": "invalid_details", "fields": {…}}` and writes nothing at all, coordinates included. |
| `/api/edit/resorts/create/` | `api:edit_resort_create` | POST | Superuser-only. JSON body `{name, canton?, latitude, longitude, details?}`; same coordinate and `details` rules as `save`. The parent region comes from the pin; an omitted `canton` is inherited from that region's existing resorts. Returns `201` with the same body shape `save` answers with (a catalogue entry plus geocode provenance). Errors: `400 invalid_identity` (blank/over-long name, or a canton the region cannot supply), `400 no_region`, `409 duplicate_name`. |

All three endpoints 404 for a caller who is not a superuser
(`_require_edit_map_admin()` in `apps/public/api.py` raises `Http404`) —
404 rather than 403, so the URL admits nothing about what sits behind it.
The page itself silently falls back to the normal map when `?edit=resorts`
is set by anyone else (`apps/public/views.py`), so the URL is safe to
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
