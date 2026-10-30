/*
 * tests/js/test_offline_warm_serves.js — what a download warms, the worker
 * serves offline (SNOW-1314).
 *
 * The bug class this guards against: a producer composes a url, the worker
 * warms it into the cache, and the fetch listener never reads it back —
 * because the url's class is ``'network'``, or its key differs from the one
 * the read looks up, or the read wants a principal stamp the warm never
 * wrote. Each of SNOW-965, SNOW-1312 and SNOW-1313 fixed one family of that
 * after a reader found it offline. This file finds the next one at build
 * time instead.
 *
 * How
 * ---
 * One table of url FAMILIES, each tied to the producer that emits it and
 * the worker class it must land in. For every family, every url the real
 * producer emits is warmed through the real ``_warmCache`` (or, for the
 * install-time lists, the real ``install`` handler), the network is taken
 * away, a real ``fetch`` event is dispatched at the real listener, and the
 * answer must be a 200 carrying that url's own body. Taking the network
 * away is done both ways a device loses it: the radio off (``fetch``
 * rejects, the worker in ``auto``) and the user's Offline mode
 * (``_forceOffline``, where the read must not touch the network at all).
 * Run with the cache core (the shipped path, ``importScripts``) and without
 * it (the worker's inline fallback), so the two classifiers cannot drift.
 *
 * The producers are the real ones: ``areaContentURLs`` and ``areaItemURLs``
 * (basemap_download_core.js), with inputs chosen to take each output
 * branch; the shell pages (``SHELL_PAGES``) and what the worker itself
 * derives from their HTML (``_shellSubresources``, ``_shellBootFeeds``);
 * and the two install-time lists, ``PRECACHE_URLS`` (one atomic
 * ``addAll`` — one failure fails install) and ``AUDIT_SCRIPTS`` (one
 * tolerant ``add`` each — a failure fails nothing). The weather sheet's
 * undated branch (no ``weatherDay``) is not a family: no caller omits the
 * day, and the map never asks for an undated sheet.
 *
 * Principal scoping
 * -----------------
 * Navigation, a trip's route and a pin's place sheet are kept for the
 * reader they were rendered for: after an account swap or a sign-out the
 * warmed copy must NOT be served. The public classes — the weather sheet,
 * the region sheet, the shell's static files and feeds — must be served
 * whoever is signed in. The two attested classes (``private-json``,
 * ``private-partial``) are fixtured with the server's
 * ``X-Snowdesk-Principal``, and a warm without it must fail, which is what
 * proves the fixture is not passing for the wrong reason. The public ones
 * are fixtured without it.
 *
 * What fails the build
 * --------------------
 * - A family whose urls classify ``'network'`` (no offline read), unless
 *   ``UNSERVED_FAMILIES`` names it with a ticket or its IndexedDB reader.
 *   An allowlisted family that has since been given a read fails too, so
 *   the entry is removed rather than left to rot.
 * - A url a producer emits that no family claims — a new output branch.
 *   ``areaContentURLs``'s output lists are checked by name, and the
 *   ``opts.*`` / ``tpl.*`` / ``items.*`` inputs each producer reads are
 *   read off its own source, so a producer that starts reading a new
 *   template or item kind fails here until this file supplies it — and
 *   with it, a family for what it emits.
 * - A ``data-*-url`` (or ``data-*-url-template``) on ``#map`` that
 *   ``MAP_DATA_URLS`` does not classify as worker cache, IndexedDB
 *   overlay, network-only by design, or a ticketed hole. Each entry's
 *   sample url is held to the worker class it claims, so a hole that gets
 *   fixed (or a worker-cached url that stops being one) fails here too.
 *
 * Reading ``#map``'s attributes from the template SOURCE, not a render
 * ------------------------------------------------------------------
 * ``_map_embed.html`` emits several of them only under a condition
 * (``favourites_eligible``, ``routes_eligible``, ``route_drawing_enabled``,
 * ``terrain_filter_eligible``). A render is one request's view and would
 * miss whichever its context switched off, so a pytest-generated fixture
 * would need a context with every switch on and a second moving part to
 * keep in step. The source names every attribute any request can get, with
 * no Django at all: strip the ``{# #}`` comments (their text contains
 * ``>``), cut the ``id="map"`` opening tag at its first ``>``, and read
 * the attribute names. A sanity floor (a known attribute must be found)
 * keeps a broken parse from passing vacuously.
 *
 * The url templates the producers are given mirror ``#map``'s values
 * (``{% url %}`` reversals of the routes in apps/public/urls.py,
 * apps/public/api_urls.py and apps/trips/urls.py); the same paths are the
 * ``MAP_DATA_URLS`` samples, so both halves read one set.
 *
 * Out of scope: tiles (tests/offline/ covers them weekly), Playwright, and
 * booting map.js in jsdom.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/basemap_cache_core.js';
import '../../static/js/basemap_download_core.js';

import {
  ORIGIN,
  basicResponse,
  dispatchFetch,
  dispatchInstall,
  flush,
  loadSw,
  makeCaches,
  pageHtml,
  resetDb,
  setStoredPrincipal,
  settleHydration,
} from './helpers/sw_harness.js';

const cacheCore = self.pwaBasemapCacheCore;
const download = self.pwaBasemapDownloadCore;

const READER = 'acct-a';
const OTHER_READER = 'acct-b';
const TODAY = '2026-10-30';
const TOMORROW = '2026-10-31';
const TRIP = '2f1c0b7e-4b7a-4c39-9a51-0d6c1f0e9a11';
const PIN = '7d3e9a10-5c2b-4f6e-8a1d-2b9c0e4f6a12';
const BARE_PIN = '0c8b1e2d-3f4a-4b5c-9d6e-7f8091a2b3c4';
const NIL_UUID = '00000000-0000-0000-0000-000000000000';

// ``#map``'s templates, as `_map_embed.html` renders them.
const TEMPLATES = {
  regionSheet: '/partials/region-sheet/XX-0000/',
  weatherDetail: '/api/weather/__SHORTID__/detail/',
  ratings: '/api/ratings/',
  tripPage: '/trips/__UUID__/',
  tripRoute: '/trips/__UUID__/route.geojson',
  favouriteSheet: `/partials/place-sheet/favourite/${NIL_UUID}/`,
};

// What `/api/area-content/` answers, shaped to take every output branch:
// two regions (one CH, one FR), two days, one weather point.
const CONTENT_INPUT = {
  regions: [
    { id: 'CH-4115', slug: 'martigny-verbier' },
    { id: 'FR-01', slug: 'chablais' },
  ],
  weather: [{ short_id: 'Ab3dE_fGh1J' }],
  days: download.forwardContentDays(TODAY, TOMORROW),
  weatherDetailTemplate: TEMPLATES.weatherDetail,
  weatherDay: TODAY,
  regionSheetTemplate: TEMPLATES.regionSheet,
  ratingsUrl: TEMPLATES.ratings,
};

// What `/api/area-items/` answers: a trip, a pin that borrows a forecast
// and a pin that borrows none.
const ITEMS_INPUT = {
  trips: [{ uuid: TRIP }],
  pins: [
    { uuid: PIN, forecast: { short_id: 'Zz9yX_wVu8T' } },
    { uuid: BARE_PIN, forecast: null },
  ],
};
const ITEM_TEMPLATES = {
  tripPage: TEMPLATES.tripPage,
  tripRoute: TEMPLATES.tripRoute,
  weatherDetail: TEMPLATES.weatherDetail,
  weatherDay: TODAY,
  favouriteSheet: TEMPLATES.favouriteSheet,
};

// The map page as the server renders it: the scripts and stylesheet it
// boots from and the day its scrubber opens on. `_shellSubresources` and
// `_shellBootFeeds` derive their families from this.
const MAP_PAGE_HEAD =
  '<script src="/static/js/map.3f9a1c.js"></script>' +
  '<link rel="stylesheet" href="/static/css/output.7c1d2e.css">' +
  `<div id="season-scrubber" data-today="${TODAY}"></div>`;

// The classes the fetch listener answers from the cache. `'network'` is
// the one it never does.
const OFFLINE_READS = new Set([
  'static',
  'navigate',
  'private-json',
  'public-json',
  'public-partial',
  'private-partial',
]);

// The classes whose warm keeps an answer only when the server attests the
// viewer (`X-Snowdesk-Principal`).
const ATTESTED_CLASSES = new Set(['private-json', 'private-partial']);

const SHA = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const DAY = '\\d{4}-\\d{2}-\\d{2}';

/*
 * The families. ``producer`` names where the urls come from (see
 * ``producedUrls``); ``match`` claims a url within that producer's output;
 * ``swClass`` is the worker class it must classify as; ``principal`` is
 * ``'scoped'`` (served only to the reader it was warmed for) or
 * ``'independent'`` (served whoever is signed in).
 */
const FAMILIES = [
  {
    name: 'bulletin page',
    producer: 'areaContentURLs.bulletinUrls',
    match: new RegExp(`^/[a-z]{2}-[a-z0-9-]+/[a-z0-9-]+/${DAY}/$`),
    swClass: 'navigate',
    principal: 'scoped',
  },
  {
    name: 'region sheet',
    producer: 'areaContentURLs.regionSheetUrls',
    match: new RegExp(`^/partials/region-sheet/[a-z0-9-]+/\\?d=${DAY}$`),
    swClass: 'public-partial',
    principal: 'independent',
  },
  {
    name: 'dated ratings',
    producer: 'areaContentURLs.ratingsUrls',
    match: new RegExp(`^/api/ratings/\\?d=${DAY}&country=[a-z]{2}$`),
    swClass: 'static',
    principal: 'independent',
  },
  {
    name: 'area weather sheet',
    producer: 'areaContentURLs.weatherDetailUrls',
    match: new RegExp(`^/api/weather/[A-Za-z0-9_-]+/detail/\\?date=${DAY}$`),
    swClass: 'public-json',
    principal: 'independent',
  },
  {
    name: 'trip page',
    producer: 'areaItemURLs',
    match: new RegExp(`^/trips/${SHA}/$`),
    swClass: 'navigate',
    principal: 'scoped',
  },
  {
    name: 'trip route',
    producer: 'areaItemURLs',
    match: new RegExp(`^/trips/${SHA}/route\\.geojson$`),
    swClass: 'private-json',
    principal: 'scoped',
  },
  {
    name: 'pin weather sheet',
    producer: 'areaItemURLs',
    match: new RegExp(`^/api/weather/[A-Za-z0-9_-]+/detail/\\?date=${DAY}$`),
    swClass: 'public-json',
    principal: 'independent',
  },
  {
    name: 'pin place sheet',
    producer: 'areaItemURLs',
    match: new RegExp(`^/partials/place-sheet/favourite/${SHA}/\\?d=${DAY}$`),
    swClass: 'private-partial',
    principal: 'scoped',
  },
  {
    name: 'shell page',
    producer: 'SHELL_PAGES',
    match: /^\/[a-z]+\/$/,
    swClass: 'navigate',
    principal: 'scoped',
  },
  {
    name: 'shell subresource',
    producer: '_shellSubresources',
    match: /^\/static\/.+\.(?:js|css)$/,
    swClass: 'static',
    principal: 'independent',
  },
  {
    name: 'boot feed',
    producer: '_shellBootFeeds',
    match: /^\/api\/(?:regions\.geojson|ratings\/)\?/,
    swClass: 'static',
    principal: 'independent',
  },
  {
    name: 'precache',
    producer: 'PRECACHE_URLS',
    match: /^\/static\//,
    swClass: 'install',
    principal: 'independent',
  },
  {
    name: 'audit script',
    producer: 'AUDIT_SCRIPTS',
    match: /^\/static\/js\/.+\.js$/,
    swClass: 'install',
    principal: 'independent',
  },
];

/*
 * Families a producer emits that the worker does not serve offline YET.
 * Each names its ticket (``ticket: 'SNOW-123'``) or the IndexedDB reader
 * that serves it instead (``indexedDb: 'pwaSomething'``). Empty: every
 * family above is served. An entry for a family that has since been given
 * an offline read fails the build, so the list only ever shrinks by hand.
 */
const UNSERVED_FAMILIES = {};

/*
 * Every url-bearing data attribute on ``#map``, classified. ``sample`` is a
 * url of that attribute's shape; ``swClass`` is what ``_classifySync``
 * must call it (``null`` for cross-origin); ``request: 'navigate'`` marks
 * the ones the browser opens as a page.
 *
 * Kinds:
 *   worker-cache       — served offline by the worker (its class reads it).
 *   indexeddb          — stored by the download in IndexedDB and read back
 *                        by ``reader`` (resource ``resource``), not the
 *                        worker.
 *   network-by-design  — needs the server, and ``reason`` says why.
 *   hole               — should open offline and does not; ``ticket``.
 *
 * ``SNOW-TBD``: a hole this ticket found, to be replaced with its ticket id
 * before merge. Nothing else may carry it.
 */
const MAP_DATA_URLS = {
  'data-regions-url': {
    kind: 'worker-cache',
    sample: '/api/regions.geojson?country=ch',
    swClass: 'static',
  },
  'data-major-regions-url': {
    kind: 'worker-cache',
    sample: '/api/major-regions.geojson?country=ch',
    swClass: 'static',
  },
  'data-sub-regions-url': {
    kind: 'worker-cache',
    sample: '/api/sub-regions.geojson?country=ch',
    swClass: 'static',
  },
  'data-ratings-url': {
    kind: 'worker-cache',
    sample: `/api/ratings/?d=${TODAY}&country=ch`,
    swClass: 'static',
  },
  'data-resorts-url': {
    kind: 'worker-cache',
    sample: '/api/resorts-by-region/',
    swClass: 'static',
  },
  'data-resorts-geojson-url': {
    kind: 'worker-cache',
    sample: '/api/resorts.geojson',
    swClass: 'static',
  },
  'data-bulletin-groupings-url': {
    kind: 'worker-cache',
    sample: `/api/bulletin-groupings.geojson?d=${TODAY}`,
    swClass: 'static',
  },
  'data-region-sheet-url': {
    kind: 'worker-cache',
    sample: `/partials/region-sheet/ch-4115/?d=${TODAY}`,
    swClass: 'public-partial',
  },
  'data-favourite-sheet-url': {
    kind: 'worker-cache',
    sample: `/partials/place-sheet/favourite/${PIN}/?d=${TODAY}`,
    swClass: 'private-partial',
  },
  'data-weather-detail-url': {
    kind: 'worker-cache',
    sample: `/api/weather/Ab3dE_fGh1J/detail/?date=${TODAY}`,
    swClass: 'public-json',
  },
  'data-trip-route-url-template': {
    kind: 'worker-cache',
    sample: `/trips/${TRIP}/route.geojson`,
    swClass: 'private-json',
  },
  'data-trip-page-url-template': {
    kind: 'worker-cache',
    sample: `/trips/${TRIP}/`,
    swClass: 'navigate',
    request: 'navigate',
  },
  'data-terrain-class-tile-url': {
    // Cross-origin tiles, pinned by a download into the area's basemap
    // bucket; the weekly offline suite (tests/offline/) covers them.
    kind: 'worker-cache',
    sample: 'https://tiles.snowdesk-data.info/terrain-class/v1/14/8529/5800.png',
    swClass: null,
  },
  'data-favourites-url': {
    kind: 'indexeddb',
    sample: '/favourites/favourites.geojson',
    swClass: 'network',
    reader: 'pwaMapOverlayCache',
    resource: 'favourites',
  },
  'data-routes-url': {
    kind: 'indexeddb',
    sample: '/routes/routes.geojson',
    swClass: 'network',
    reader: 'pwaMapOverlayCache',
    resource: 'routes',
  },
  'data-weather-url': {
    kind: 'indexeddb',
    sample: '/api/weather.geojson',
    swClass: 'network',
    reader: 'pwaMapOverlayCache',
    resource: 'weather',
  },
  'data-community-reports-url': {
    kind: 'indexeddb',
    sample: '/api/community-reports.geojson',
    swClass: 'network',
    reader: 'pwaMapOverlayCache',
    resource: 'community_reports',
  },
  'data-area-content-url': {
    kind: 'network-by-design',
    sample: '/api/area-content/?bbox=7,46,8,47',
    swClass: 'network',
    reason: 'plans a download, which needs the network to fetch what it names',
  },
  'data-area-items-url': {
    kind: 'network-by-design',
    sample: '/api/area-items/?bbox=7,46,8,47',
    swClass: 'network',
    reason: 'plans a download; private and never cached (an-area-download-carries-your-trips)',
  },
  'data-trip-share-route-url-template': {
    kind: 'network-by-design',
    sample: '/trips/s/some-token/route.geojson',
    swClass: 'network',
    reason: 'a share-token route attests nobody and is no-store (an-area-download-carries-your-trips)',
  },
  'data-resort-create-url': {
    kind: 'network-by-design',
    sample: '/favourites/partials/resort/create/',
    swClass: 'network',
    reason: 'a POST; offline it is queued by pwaMutationQueue, not served',
  },
  'data-route-claim-url-template': {
    kind: 'network-by-design',
    sample: '/routes/partials/share/__TOKEN__/claim/',
    swClass: 'network',
    reason: 'a POST that copies a shared route onto the account; the server must do it',
  },
  'data-route-share-dismiss-url-template': {
    kind: 'network-by-design',
    sample: '/routes/s/__TOKEN__/dismiss/',
    swClass: 'network',
    reason: 'a POST that drops the example from the session; the server must do it',
  },
  'data-routes-signin-url': {
    kind: 'network-by-design',
    sample: '/account/sign-in/',
    swClass: 'navigate',
    request: 'navigate',
    reason: 'signing in needs the server',
  },
  'data-resort-sheet-url': {
    kind: 'hole',
    sample: `/partials/place-sheet/resort/verbier/?d=${TODAY}`,
    swClass: 'network',
    ticket: 'SNOW-TBD',
    hole: 'a resort tapped offline says "Place details are unavailable offline" over a downloaded area',
  },
  'data-ski-summits-url': {
    kind: 'hole',
    sample: '/api/ski-summits.json',
    swClass: 'network',
    ticket: 'SNOW-TBD',
    hole: "search's SAC summit rows are absent offline: the feed is neither worker-cached nor in IndexedDB",
  },
};

const MAP_DATA_KINDS = new Set(['worker-cache', 'indexeddb', 'network-by-design', 'hole']);

// ---------------------------------------------------------------------------
// Producers
// ---------------------------------------------------------------------------

/**
 * Every url each producer emits, keyed by producer name. Relative to the
 * origin, as the producers emit them (the worker's own two derive absolute
 * urls, which are made relative here so one matcher shape serves both).
 *
 * @param {object} sw - a ``loadSw`` sandbox.
 * @returns {Object<string, string[]>}
 */
function producedUrls(sw) {
  const content = download.areaContentURLs(CONTENT_INPUT);
  const planned = Object.values(content).flat();
  const items = download.areaItemURLs(ITEMS_INPUT, ITEM_TEMPLATES, planned);
  const relative = (url) => url.replace(ORIGIN, '');
  const out = { areaItemURLs: items };
  for (const [list, urls] of Object.entries(content)) out[`areaContentURLs.${list}`] = urls;
  out.SHELL_PAGES = [...sw.SHELL_PAGES];
  out._shellSubresources = sw._shellSubresources(mapPageHtml(READER)).map(relative);
  out._shellBootFeeds = sw._shellBootFeeds(mapPageHtml(READER)).map(relative);
  out.PRECACHE_URLS = [...sw.PRECACHE_URLS];
  out.AUDIT_SCRIPTS = [...sw.AUDIT_SCRIPTS];
  return out;
}

/** Every family that claims ``url`` within ``producer``'s output. */
function familiesFor(producer, url) {
  return FAMILIES.filter((f) => f.producer === producer && f.match.test(url));
}

/** The urls of one family, from a fresh sandbox's producers. */
function familyUrls(family) {
  const produced = producedUrls(loadSw());
  return (produced[family.producer] || []).filter((url) => family.match.test(url));
}

/**
 * The input keys a producer reads, off its own source: ``opts.x``,
 * ``tpl.x`` and ``items.x``.
 *
 * @param {Function} fn
 * @param {string} name - the parameter alias.
 * @returns {string[]}
 */
function readsOf(fn, name) {
  const pattern = new RegExp(`\\b${name}\\.([A-Za-z_]+)`, 'g');
  return [...new Set([...fn.toString().matchAll(pattern)].map((m) => m[1]))].sort();
}

// ---------------------------------------------------------------------------
// The server, as fixtures
// ---------------------------------------------------------------------------

/** The map page, rendered for ``who`` (``''`` for anonymous). */
function mapPageHtml(who) {
  return pageHtml(who, `${MAP_PAGE_HEAD}<main>map</main>`);
}

/** The class a url is fixtured as — its family's, across every producer. */
function fixtureClass(path) {
  if (path.startsWith('/static/')) return 'static';
  const family = FAMILIES.find((f) => f.match.test(path));
  return family ? family.swClass : null;
}

/** The body the server answers ``path`` with — unique to the url. */
function bodyFor(path, who) {
  const cls = fixtureClass(path);
  if (path === '/map/') return mapPageHtml(who);
  if (cls === 'navigate') return pageHtml(who, `<main>${path}</main>`);
  if (cls === 'public-partial' || cls === 'private-partial') return `<section>${path}</section>`;
  if (path.startsWith('/api/regions.geojson')) {
    return JSON.stringify({ type: 'FeatureCollection', features: [{ id: path }] });
  }
  return JSON.stringify({ url: path });
}

/**
 * The server's answer for ``path``, rendered for ``who``. The attested
 * classes name their viewer in ``X-Snowdesk-Principal`` unless
 * ``attest`` is false; nothing else carries it.
 *
 * @param {string} path
 * @param {string} who
 * @param {{attest?: boolean}} [opts]
 * @returns {object}
 */
function answerFor(path, who, opts = {}) {
  const cls = fixtureClass(path);
  if (cls === null) return basicResponse('not found', { status: 404 });
  const headers = {};
  if (cls === 'navigate' || cls === 'public-partial' || cls === 'private-partial') {
    headers['Content-Type'] = 'text/html; charset=utf-8';
  } else if (path.endsWith('.js')) {
    headers['Content-Type'] = 'text/javascript';
  } else if (path.endsWith('.css')) {
    headers['Content-Type'] = 'text/css';
  } else {
    headers['Content-Type'] = 'application/json';
  }
  if (cls === 'public-partial') headers['Cache-Control'] = 'private, max-age=600';
  if (ATTESTED_CLASSES.has(cls)) {
    headers['Cache-Control'] = 'private, no-cache';
    if (opts.attest !== false) headers['X-Snowdesk-Principal'] = who;
  }
  return basicResponse(bodyFor(path, who), { headers });
}

/**
 * A network the test can switch off, answering every same-origin url from
 * ``answerFor`` for whoever the server thinks is signed in.
 *
 * @param {{attest?: boolean}} [opts]
 * @returns {{state: {online: boolean}, fetch: Function}}
 */
function makeNetwork(opts = {}) {
  const state = { online: true };
  const fetch = vi.fn(async (input) => {
    if (!state.online) throw new TypeError('Failed to fetch');
    const url = new URL(typeof input === 'string' ? input : input.url, ORIGIN);
    return answerFor(url.pathname + url.search, READER, opts);
  });
  return { state, fetch };
}

// ---------------------------------------------------------------------------
// The reader, as requests
// ---------------------------------------------------------------------------

/** The request the page makes for ``path``, given the class it lands in. */
function requestFor(path, cls) {
  const url = new URL(path, ORIGIN).toString();
  if (cls === 'navigate') return { url, method: 'GET', mode: 'navigate', destination: 'document' };
  if (cls === 'public-partial' || cls === 'private-partial') {
    return {
      url,
      method: 'GET',
      mode: 'cors',
      destination: '',
      headers: new Headers({ 'HX-Request': 'true' }),
    };
  }
  if (path.endsWith('.js')) return { url, method: 'GET', mode: 'no-cors', destination: 'script' };
  if (path.endsWith('.css')) return { url, method: 'GET', mode: 'no-cors', destination: 'style' };
  return { url, method: 'GET', mode: 'cors', destination: '' };
}

/** What the listener answered: status and body, or ``error`` when it rejected. */
async function served(sw, request) {
  try {
    const response = await dispatchFetch(sw, request);
    if (response === null) return { status: 'passthrough', body: null };
    return { status: response.status, body: await response.text() };
  } catch (_err) {
    return { status: 'error', body: null };
  }
}

/** The worker class ``path`` is read as, by its own request. */
function swClassOf(sw, path, cls) {
  return sw._classifySync(requestFor(path, cls), new URL(path, ORIGIN));
}

// The two ways a device loses the network.
const OFFLINE_MODES = {
  'the radio off': async (network) => {
    network.state.online = false;
  },
  'Offline mode': async (network, sw) => {
    network.state.online = false;
    await settleHydration(sw);
    sw._forceOffline();
  },
};

const VARIANTS = [
  ['with the cache core', { core: cacheCore }],
  ['on the inline fallback', {}],
];

// ---------------------------------------------------------------------------
// The guard
// ---------------------------------------------------------------------------

describe('every produced url belongs to a family', () => {
  it('claims each url a producer emits exactly once', () => {
    const produced = producedUrls(loadSw());
    const unclaimed = [];
    const ambiguous = [];
    for (const [producer, urls] of Object.entries(produced)) {
      for (const url of urls) {
        const claims = familiesFor(producer, url);
        if (claims.length === 0) unclaimed.push(`${producer}: ${url}`);
        if (claims.length > 1) ambiguous.push(`${producer}: ${url}`);
      }
    }
    expect(unclaimed, 'a new output branch — add a family for it').toEqual([]);
    expect(ambiguous).toEqual([]);
  });

  it('exercises every family: none is empty', () => {
    const empty = FAMILIES.filter((f) => familyUrls(f).length === 0).map((f) => f.name);
    expect(empty).toEqual([]);
  });

  it("knows each of areaContentURLs's output lists", () => {
    const lists = Object.keys(download.areaContentURLs(CONTENT_INPUT)).sort();
    const known = FAMILIES.map((f) => f.producer)
      .filter((p) => p.startsWith('areaContentURLs.'))
      .map((p) => p.slice('areaContentURLs.'.length))
      .sort();
    expect(lists).toEqual(known);
  });

  it('supplies every input the producers read', () => {
    // A producer that starts reading a new template or item kind emits a
    // family this file has never warmed. Read off the source, so it fails
    // here first.
    expect(readsOf(download.areaContentURLs, 'opts')).toEqual(Object.keys(CONTENT_INPUT).sort());
    expect(readsOf(download.areaItemURLs, 'tpl')).toEqual(Object.keys(ITEM_TEMPLATES).sort());
    expect(readsOf(download.areaItemURLs, 'items')).toEqual(Object.keys(ITEMS_INPUT).sort());
  });
});

describe.each(VARIANTS)('every produced family has an offline read (%s)', (_label, options) => {
  it('classifies each family as it declares, and none as network unless allowlisted', () => {
    const sw = loadSw(options);
    const unserved = [];
    const misclassified = [];
    for (const family of FAMILIES) {
      if (family.swClass === 'install') continue;
      for (const url of familyUrls(family)) {
        const actual = swClassOf(sw, url, family.swClass);
        if (!OFFLINE_READS.has(actual) && !UNSERVED_FAMILIES[family.name]) {
          unserved.push(`${family.name}: ${url} is '${actual}'`);
        } else if (actual !== family.swClass && !UNSERVED_FAMILIES[family.name]) {
          misclassified.push(`${family.name}: ${url} is '${actual}', not '${family.swClass}'`);
        }
      }
    }
    expect(unserved, 'warmed but never read offline').toEqual([]);
    expect(misclassified).toEqual([]);
  });

  it('holds every allowlist entry to a ticket or a reader, and to still being needed', () => {
    const sw = loadSw(options);
    for (const [name, entry] of Object.entries(UNSERVED_FAMILIES)) {
      const family = FAMILIES.find((f) => f.name === name);
      expect(family, `${name} is not a family`).toBeTruthy();
      expect(
        /^SNOW-\d+$/.test(entry.ticket || '') || /^pwa[A-Z]\w+$/.test(entry.indexedDb || ''),
        `${name} names neither a ticket nor an IndexedDB reader`,
      ).toBe(true);
      const stillUnserved = familyUrls(family).some(
        (url) => !OFFLINE_READS.has(swClassOf(sw, url, family.swClass)),
      );
      expect(stillUnserved, `${name} is served now — drop its allowlist entry`).toBe(true);
    }
  });
});

describe.each(VARIANTS)('a warmed url is served offline (%s)', (_label, options) => {
  beforeEach(async () => {
    await resetDb();
    await setStoredPrincipal(READER);
  });

  // The families warmed through `_warmCache`, each by what a caller hands
  // it: the area's own urls, or the shell pages whose HTML names the
  // subresources and boot feeds.
  const warmed = FAMILIES.filter((f) => f.swClass !== 'install' && !UNSERVED_FAMILIES[f.name]);
  const warmList = (family) =>
    ['shell subresource', 'boot feed'].includes(family.name)
      ? loadSw().SHELL_PAGES
      : familyUrls(family);

  for (const family of warmed) {
    for (const [mode, goOffline] of Object.entries(OFFLINE_MODES)) {
      it(`${family.name}, with ${mode}`, async () => {
        const caches = makeCaches();
        const network = makeNetwork();
        const sw = loadSw({ ...options, caches, fetch: network.fetch });
        const urls = familyUrls(family);

        const result = await sw._warmCache(warmList(family));
        expect(result).toMatchObject({ failed: 0, cancelled: false });
        await flush();
        await goOffline(network, sw);
        network.fetch.mockClear();

        for (const url of urls) {
          const answer = await served(sw, requestFor(url, family.swClass));
          expect(answer, `${url} offline`).toEqual({ status: 200, body: bodyFor(url, READER) });
        }
        if (mode === 'Offline mode') expect(network.fetch).not.toHaveBeenCalled();

        // Who may read it back.
        for (const reader of [OTHER_READER, null]) {
          await setStoredPrincipal(reader);
          for (const url of urls) {
            const answer = await served(sw, requestFor(url, family.swClass));
            if (family.principal === 'scoped') {
              expect(answer.body, `${url} served to ${reader}`).not.toBe(bodyFor(url, READER));
              expect(answer.status).not.toBe(200);
            } else {
              expect(answer, `${url} for ${reader}`).toEqual({
                status: 200,
                body: bodyFor(url, READER),
              });
            }
          }
        }
      });
    }
  }

  it('needs the server attestation where the class does, and nowhere else', async () => {
    for (const family of warmed) {
      for (const url of familyUrls(family)) {
        const fixture = answerFor(url, READER);
        const attested = fixture.headers.get('X-Snowdesk-Principal');
        if (ATTESTED_CLASSES.has(family.swClass)) {
          expect(attested, url).toBe(READER);
          const caches = makeCaches();
          const unattested = makeNetwork({ attest: false });
          const sw = loadSw({ ...options, caches, fetch: unattested.fetch });
          expect(await sw._warmCache([url]), `${url} kept unattested`).toMatchObject({
            ok: 0,
            failed: 1,
          });
        } else {
          expect(attested, url).toBeNull();
        }
      }
    }
  });
});

describe.each(VARIANTS)('the install-time lists are served offline (%s)', (_label, options) => {
  beforeEach(async () => {
    await resetDb();
  });

  /**
   * The offline read for an install-time url. The fallback page is never
   * asked for by url — it is what a navigation the device does not hold
   * is answered with — so it is read that way.
   */
  async function readInstalled(sw, url) {
    if (url === sw.OFFLINE_FALLBACK) {
      const answer = await served(sw, requestFor('/never-held/', 'navigate'));
      return { status: answer.status, body: answer.body };
    }
    return served(sw, requestFor(url, 'static'));
  }

  for (const [mode, goOffline] of Object.entries(OFFLINE_MODES)) {
    it(`PRECACHE_URLS and AUDIT_SCRIPTS, with ${mode}`, async () => {
      const network = makeNetwork();
      const caches = makeCaches({ fetch: network.fetch });
      const sw = loadSw({ ...options, caches, fetch: network.fetch });

      await dispatchInstall(sw);
      await goOffline(network, sw);
      network.fetch.mockClear();

      for (const reader of [null, READER, OTHER_READER]) {
        await setStoredPrincipal(reader);
        for (const url of [...sw.PRECACHE_URLS, ...sw.AUDIT_SCRIPTS]) {
          const answer = await readInstalled(sw, url);
          expect(answer, `${url} for ${reader}`).toEqual({
            status: 200,
            body: bodyFor(url, READER),
          });
        }
      }
      if (mode === 'Offline mode') expect(network.fetch).not.toHaveBeenCalled();
    });
  }

  it('PRECACHE_URLS is atomic: one failure fails install and keeps none', async () => {
    const network = makeNetwork();
    const sw0 = loadSw();
    const failing = sw0.PRECACHE_URLS[sw0.PRECACHE_URLS.length - 1];
    const fetch = vi.fn(async (input) => {
      const url = new URL(typeof input === 'string' ? input : input.url, ORIGIN);
      if (url.pathname === failing) return basicResponse('gone', { status: 404 });
      return network.fetch(input);
    });
    const caches = makeCaches({ fetch });
    const sw = loadSw({ ...options, caches, fetch });

    await expect(dispatchInstall(sw)).rejects.toThrow();
    expect(caches.size('snowdesk-shell-UNSUBSTITUTED')).toBe(0);
  });

  it('AUDIT_SCRIPTS is tolerant: a failure fails nothing, and the rest are kept', async () => {
    const network = makeNetwork();
    const sw0 = loadSw();
    const [failing, ...kept] = sw0.AUDIT_SCRIPTS;
    const fetch = vi.fn(async (input) => {
      const url = new URL(typeof input === 'string' ? input : input.url, ORIGIN);
      if (url.pathname === failing) return basicResponse('gone', { status: 404 });
      return network.fetch(input);
    });
    const caches = makeCaches({ fetch });
    const sw = loadSw({ ...options, caches, fetch });

    await dispatchInstall(sw);
    network.state.online = false;

    expect((await readInstalled(sw, failing)).status).not.toBe(200);
    for (const url of [...sw.PRECACHE_URLS, ...kept]) {
      expect((await readInstalled(sw, url)).status, url).toBe(200);
    }
  });
});

// ---------------------------------------------------------------------------
// #map's data urls
// ---------------------------------------------------------------------------

/**
 * Every ``data-*-url`` / ``data-*-url-template`` attribute on ``#map``'s
 * opening tag, read from the template source (see the module header).
 *
 * @returns {string[]}
 */
function mapDataUrlAttributes() {
  const source = readFileSync(
    join(process.cwd(), 'apps', 'public', 'templates', 'public', 'partials', '_map_embed.html'),
    'utf8',
  );
  const stripped = source.replace(/\{#[\s\S]*?#\}/g, '').replace(/\{%[\s\S]*?%\}/g, ' ');
  const start = stripped.search(/<div\s+id="map"/);
  if (start === -1) return [];
  const tag = stripped.slice(start, stripped.indexOf('>', start));
  const names = [...tag.matchAll(/\s(data-[a-z0-9-]+)=/g)].map((m) => m[1]);
  return [...new Set(names.filter((n) => /-url(?:-template)?$/.test(n)))].sort();
}

describe("#map's data urls are each classified", () => {
  it('reads the attributes off the template', () => {
    const attributes = mapDataUrlAttributes();
    // The floor: a parse that found nothing would otherwise pass.
    expect(attributes).toContain('data-regions-url');
    expect(attributes).toContain('data-trip-page-url-template');
  });

  it('classifies every one, and nothing that is not there', () => {
    expect(mapDataUrlAttributes()).toEqual(Object.keys(MAP_DATA_URLS).sort());
  });

  it.each(Object.entries(MAP_DATA_URLS))('%s', (_name, entry) => {
    expect(MAP_DATA_KINDS.has(entry.kind)).toBe(true);
    for (const [label, options] of VARIANTS) {
      const sw = loadSw(options);
      const url = new URL(entry.sample, ORIGIN);
      const cls = sw._classifySync(requestFor(entry.sample, entry.request || 'json'), url);
      expect(cls, `${entry.sample} ${label}`).toBe(entry.swClass);
    }
    if (entry.kind === 'worker-cache' && entry.swClass !== null) {
      expect(OFFLINE_READS.has(entry.swClass)).toBe(true);
    }
    if (entry.kind === 'indexeddb') {
      // The reader is real, and is the store the download writes this
      // resource into.
      const reader = readFileSync(
        join(process.cwd(), 'static', 'js', 'map_overlay_offline_cache.js'),
        'utf8',
      );
      expect(reader.includes(`'${entry.reader}'`), entry.reader).toBe(true);
      const writer = readFileSync(
        join(process.cwd(), 'static', 'js', 'map_basemap_downloads.js'),
        'utf8',
      );
      expect(writer.includes(`'${entry.resource}',`), entry.resource).toBe(true);
    }
    if (entry.kind === 'network-by-design') expect(entry.reason).toBeTruthy();
    if (entry.kind === 'hole') {
      expect(entry.ticket).toMatch(/^SNOW-(?:\d+|TBD)$/);
      expect(entry.hole).toBeTruthy();
    }
  });
});
