/*
 * static/js/map_shared.js — the helpers every map IIFE reaches for.
 *
 * SNOW-610, step 2: extracted verbatim from map.js. Nothing here is
 * specific to one surface — these are the definitions the main init, the
 * scrubber, the timelapse, the season ribbon and the two download controls
 * all share, and which each would otherwise be tempted to re-declare.
 * Three of the duplications the SNOW-606 review found were exactly that.
 *
 * What lives here: the rating wire-format table, the date-key regex and
 * `?d=` reader, the try/catch localStorage trio, the popup date and
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

// SNOW-419: ISO timestamp -> coarse relative-time string ("2 h ago",
// "12 min ago", "just now") for the community-reports popup. Deliberately
// coarse (minutes/hours only, no seconds) to match the server's own
// 15-minute truncation of observed_at — the popup shouldn't imply more
// precision than the wire payload actually carries.
const formatRelativeTime = (isoString) => {
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return '';
  const diffMinutes = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (diffMinutes < 1) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes} min ago`;
  return `${Math.round(diffMinutes / 60)} h ago`;
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
