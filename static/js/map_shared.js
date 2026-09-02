/*
 * static/js/map_shared.js — the helpers every map IIFE reaches for.
 *
 * SNOW-610, step 2: extracted verbatim from map.js. Nothing here is
 * specific to one surface — these are the definitions the main init, the
 * scrubber, the timelapse, the season ribbon and the two download controls
 * all share, and which each would otherwise be tempted to re-declare.
 * Three of the duplications the SNOW-606 review found were exactly that.
 *
 * What lives here: the rating wire-format table, the date-key regex, the
 * `?d=` reader/writer pair and the `readDisplayDate` default-to-today read
 * on top of it (SNOW-793), the try/catch localStorage trio, the popup date and
 * relative-time formatters, the memoised season-ratings and
 * bulletin-groupings fetches, and `repaintRegionsForDate` — the one
 * choropleth repaint every date-changing surface calls.
 *
 * LOAD ORDER: a classic script, so its top-level `let`/`const` are
 * readable as bare identifiers from every file loaded after it. It must
 * load before map.js. `RATINGS_URL` and `BULLETIN_GROUPINGS_URL_MODULE`
 * are declared null here and written by map.js's main IIFE once it has
 * read the #map element's data-* attributes — the fetches below are all
 * lazy, so none of them reads either binding before that write.
 */

// Wire-format int → rating string. Inverse of public/api.py::_RATING_TO_INT.
// Hoisted so the timelapse and the scrubber share one definition.
const INT_TO_RATING = ['no_rating', 'low', 'moderate', 'considerable', 'high', 'very_high'];

// Canonical ISO date key (YYYY-MM-DD). Used to validate ``?d=`` params and
// hash-carried dates before they're handed to Date.parse or a URL builder.
const DATE_KEY_RE = /^\d{4}-\d{2}-\d{2}$/;

// Read the ``?d=YYYY-MM-DD`` query param from the current URL. Returns the
// validated string or null when the param is missing or malformed. Hoisted so
// every IIFE that reads it shares one definition of "valid".
const readUrlDateParam = () => {
  const d = new URL(location.href).searchParams.get('d');
  return d && DATE_KEY_RE.test(d) ? d : null;
};

// SNOW-793: today, as the server rendered it onto ``#season-scrubber``'s
// ``data-today``. Read from the DOM rather than from ``new Date()`` because
// the scrubber, the ribbon track and the season bounds are all placed
// against the SERVER's day — deriving a second "today" from the client
// clock would put the thumb and the painted day on different dates for
// anyone whose device is a timezone (or a wrong clock) away from it.
//
// Lazily, on every call: this file is a classic script that runs at parse
// time, and the unit tests build their fixture after importing it.
const readTodayDateParam = () => {
  const el = document.getElementById('season-scrubber');
  const d = el && el.dataset.today;
  return d && DATE_KEY_RE.test(d) ? d : null;
};

// SNOW-793: the day the map should be showing — the one the URL asked for,
// and today when it asked for none.
//
// This is the read that reverses half of SNOW-660. That ticket made a bare
// querystring mean "no day chosen" and left the map uncoloured, because the
// boot default it removed was ``effectiveTodayKey`` — the last date in the
// ratings cache carrying a visible country, which off season is one
// arbitrary day that nothing on screen names. Today is not that: it is the
// day a visitor opening an avalanche map means, and #map-date-ribbon can
// report it. So the default is back, and it is today specifically — never
// the last populated day, which is the date that bug was actually about.
//
// ``?d=`` still wins outright, so a deep link is unaffected. Null is still
// possible — a page with no scrubber, or a malformed ``data-today`` — and
// every caller still handles it as "no day known", which is what keeps
// SNOW-660's uncoloured state reachable as the honest fallback.
//
// Nothing WRITES the default to the URL: a bare URL means today, so a
// shared bare link shows the current day rather than the day it was
// copied. ``writeUrlDateParam`` stays the caller-driven commit path.
const readDisplayDate = () => readUrlDateParam() || readTodayDateParam();

// SNOW-660: write ``?d=YYYY-MM-DD`` for the day now on screen.
//
// ``replaceState``, never ``push``: a long scrub or a season's playback
// would otherwise bury the back button under dozens of intermediate dates.
// ``location.pathname`` (not a hardcoded /map/) keeps a visitor scrubbing on
// the homepage on ``/``, and the hash is preserved because it carries the
// selected region.
//
// EVERY committed day is written, today included. Today used to be spelt as
// a bare URL, on the reasoning that it was the canonical one for "now" — but
// a bare querystring now means "no day has been asked for", and the two
// cannot be the same URL or a reload would blank the map the visitor had
// just chosen to look at.
//
// Two callers, at deliberately different rates:
//
//   - map_scrubber.js's ``commitDate`` writes on EVERY commit. A drag
//     already coalesces onto one commit per release, so that is one write
//     per user action.
//   - map_timelapse.js writes only at SETTLE points — where playback stops,
//     and the skip-to-start/skip-to-end jumps — never per frame. Playback
//     commits several frames a second, and Safari throttles
//     ``replaceState`` (~100 calls per 30s) by THROWING, which would take
//     down the running timer mid-season. The intermediate frames are also
//     not days anyone asked to keep: the day that matters is the one
//     playback leaves on screen.
const writeUrlDateParam = (dateKey) => {
  history.replaceState(null, '', location.pathname + '?d=' + dateKey + location.hash);
};

// localStorage guarded by try/catch — private mode / disabled storage / quota
// throws are all silently swallowed. Boolean-typed values persist as the
// strings 'true' / 'false' (readBoolStorage handles the coercion).
const readStorage = (key) => {
  try { return localStorage.getItem(key); }
  catch (_) { return null; }
};
const writeStorage = (key, value) => {
  try { localStorage.setItem(key, value); }
  catch (_) { /* private mode — choice still applies for this session */ }
};
const readBoolStorage = (key, dflt) => {
  const v = readStorage(key);
  return v === null ? dflt : v === 'true';
};

// ``SCRUBBER_MONTHS`` / ``formatDateLong`` ("2026-04-25" → "APR 25 2026")
// went with mapDatePillInit below — that pill was their only caller.
// POPUP_MONTHS / formatDatePopup are unrelated and still live.

// SNOW-318: "2026-04-08" → "8 Apr 2026" — day-first, title-case 3-letter month.
// This mirrors the popup card's server render, where
// _region_tooltip.html formats the date with ``date:"j M Y"``, so the bulletin
// label reads identically whether the popup was just opened (server-rendered)
// or relabelled in place on a scrubber date change.
const POPUP_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const formatDatePopup = (dateKey) => {
  const [y, m, d] = dateKey.split('-');
  return `${parseInt(d, 10)} ${POPUP_MONTHS[parseInt(m, 10) - 1]} ${y}`;
};

// SNOW-419: ISO timestamp -> relative-time string ("2 hours ago") for the
// community-reports and favourite-pin popups.
//
// It formats nothing itself any more. It had its own ladder — "just now",
// "12 min ago", "2 h ago" — which was three English literals `makemessages`
// could never see, and which said a different thing from the panel row for
// the same report ("5 h ago" against "5 hours ago"). The words now come
// from static/js/relative_time.js, so a report reads identically whether
// the user reached it by tapping the pin or by opening the panel, and the
// wording is the platform's own for the page's locale rather than ours.
//
// It also used to take a granularity argument and shift by half of it,
// because the server floored observed_at to a quarter hour before sending.
// That floor is gone (see the note above community_reports_geojson in
// apps/public/api.py) and so is the correction.
//
// Returns '' when relative_time.js has not loaded, which both callers
// already treat as "no subheader" — home.html loads it, and every caller
// runs from a user gesture long after parse, so this is a guard rather
// than a case.
const formatRelativeTime = (isoString) => {
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then) || !window.pwaRelativeTime) return '';
  return window.pwaRelativeTime.format(then - Date.now());
};

// SNOW-658: the favourite pin's popup carries the same relative-time
// subheader as the observation pin's, and favourites.js builds it — but
// that module is NOT part of the map bundle, so the bare identifier above
// is not a contract it can rely on (its own load order sits in a surface
// partial, not in home.html's bundle run). Publish the formatter on a named
// channel instead, the way map_state.js publishes window.snowdeskMapState:
// one greppable name, and no second implementation of "5 h ago".
window.snowdeskMapFormat = Object.freeze({ relativeTime: formatRelativeTime });

// Lazily-fetched, cached payload from /api/ratings/?country=ch. Shape:
// { date_iso: { region_id: rating_int } }. Both timelapse (SNOW-46) and
// the scrubber (SNOW-47) consume the same dataset; sharing one fetch
// keeps the payload off the wire twice.
// SNOW-239: RATINGS_URL replaces the legacy season-ratings and today-summaries
// URLs. The base URL is set once from data-ratings-url; each consumer appends
// its own query string (?d=...&country=... for cold-open, ?country=ch for the
// full-season scrubber/timelapse cache).
let RATINGS_URL = null;
let SEASON_RATINGS_PROMISE = null;

const getSeasonRatings = () => {
  if (SEASON_RATINGS_PROMISE !== null) return SEASON_RATINGS_PROMISE;
  if (!RATINGS_URL) {
    return Promise.reject(new Error('ratings URL not set'));
  }
  SEASON_RATINGS_PROMISE = fetch(RATINGS_URL + '?country=ch').then((resp) => {
    if (!resp.ok) throw new Error('ratings fetch failed');
    return resp.json();
  });
  return SEASON_RATINGS_PROMISE;
};

// SNOW-323: Per-date bulletin-groupings fetch from /api/bulletin-groupings.geojson.
// The endpoint is single-date (?d=YYYY-MM-DD) and returns one day's
// FeatureCollection — { type: "FeatureCollection", features: [...] }. Each
// response is memoised per date for the session, so re-visiting a date the user
// has already landed on costs no network. This replaces the former whole-season
// payload: once the historical backfill landed, serialising every day's
// dissolved geometry in one response pushed the web worker past its 512 MB
// limit. Fetching one settled day at a time keeps the response — and the
// server's peak memory — bounded regardless of how deep the archive grows.
let BULLETIN_GROUPINGS_URL_MODULE = null;
const BULLETIN_GROUPINGS_BY_DATE = new Map(); // dateKey -> Promise<FeatureCollection>
const EMPTY_FEATURE_COLLECTION = { type: 'FeatureCollection', features: [] };

const fetchBulletinGroupingsForDate = (dateKey) => {
  if (!dateKey) return Promise.reject(new Error('bulletin groupings: no date'));
  const cached = BULLETIN_GROUPINGS_BY_DATE.get(dateKey);
  if (cached) return cached;
  if (!BULLETIN_GROUPINGS_URL_MODULE) {
    return Promise.reject(new Error('bulletin groupings URL not set'));
  }
  // Fetch ALL countries (no ?country= filter) so cross-border ALBINA
  // bulletins — e.g. countries: ["AT", "IT"] with no "CH" — are present.
  // Per-country visibility is handled client-side by applyCountryFilters'
  // array-membership filter on the `countries` property, so a country toggle
  // never triggers a refetch. This deliberately differs from the L1/L2
  // overlays and getSeasonRatings, which are CH-scoped at the fetch.
  const sep = BULLETIN_GROUPINGS_URL_MODULE.includes('?') ? '&' : '?';
  const url = BULLETIN_GROUPINGS_URL_MODULE + sep + 'd=' + encodeURIComponent(dateKey);
  const promise = fetch(url)
    .then((resp) => {
      if (!resp.ok) throw new Error('bulletin groupings fetch failed');
      return resp.json();
    })
    .catch((err) => {
      // Don't poison the cache on failure — drop the entry so a later
      // settle on the same date can retry.
      BULLETIN_GROUPINGS_BY_DATE.delete(dateKey);
      throw err;
    });
  BULLETIN_GROUPINGS_BY_DATE.set(dateKey, promise);
  return promise;
};

// SNOW-623: the collaborators `choropleth_core.js`'s `paintRatingsFrame`
// needs, bound once. Keeping MapLibre on this side of the boundary is what
// lets the paint semantics be unit-tested — see that module's header for
// why the two semantics must stay distinct.
const choroplethDeps = () => ({
  featureById: FEATURE_BY_REGION_ID,
  intToRating: INT_TO_RATING,
  setRating: (featureId, rating) =>
    MAP.setFeatureState({ source: 'regions', id: featureId }, { rating }),
});

// Repaint every known region's choropleth fill for the supplied date.
// `clearMissing: true` — a region with no rating on this date must go back
// to no_rating, or the previously-scrubbed day's colour lingers on it.
const repaintRegionsForDate = (dateKey, cache) => {
  if (!MAP) return;
  self.pwaChoroplethCore.paintRatingsFrame(
    choroplethDeps(),
    (cache && cache[dateKey]) || {},
    { clearMissing: true },
  );
};
