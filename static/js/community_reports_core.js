/*
 * static/js/community_reports_core.js — the pure half of the map's
 * "On the ground" layer (SNOW-1290).
 *
 * A field report is LIVE on the day it was made and RECENT for the three
 * days after; a scrubbed map shows only the reports of the day it shows,
 * as plain DAY pins. That replaced a 48-hour linear fade that emptied the
 * map overnight and drew a two-hour-old warning sign and a forty-hour-old
 * one in two barely different greys. Read
 * docs/decisions/an-observation-is-live-today-and-recent-for-three-days.md.
 *
 * The server decides which reports a fetch returns and stamps each with
 * `observed_on`, its local day — the same day boundary every bulletin
 * uses — so the browser never works out a timezone. What this module does
 * is the arithmetic `map.js` cannot test under jsdom:
 *
 *   - which state a report is in against the map's day and today, and
 *     whether it is still inside the window at all (a cached copy read
 *     back offline can be days older than the window has moved);
 *   - the short age a live pin carries ("12m", "3h");
 *   - the `?d=` a fetch for the map's day asks with;
 *   - the three family glyphs, as stroke paths on the 24 grid shared with
 *     templates/includes/_icon_observation_family.html.
 *
 * ## Exports (frozen `self.pwaCommunityReportsCore`)
 *
 *   RECENT_DAYS, FAMILIES, FAMILY_COLOURS, FAMILY_PATHS
 *   addDays(dayKey, n)
 *   reportState(observedOn, displayDate, today)
 *   withStates(geojson, displayDate, today, now)
 *   ageLabel(observedAt, now)
 *   fetchUrl(baseUrl, displayDate, today)
 *   asList(value)
 */

// @ts-check

(function () {
  'use strict';

  /** Days before today a report is still drawn, as recent. Mirrors
   * ``_COMMUNITY_RECENT_DAYS`` in apps/public/api.py. */
  const RECENT_DAYS = 3;

  /** OBSERVATION_FAMILY, most serious first — the server's order. */
  const FAMILIES = Object.freeze(['WARNING', 'SURFACE', 'TRAIL']);

  /**
   * One colour per family. MapLibre paint cannot read a CSS custom
   * property, so these are the JS side of --color-obs-warning / -surface /
   * -trail in src/css/main.css (light theme: the map is always drawn on a
   * light basemap). Change one, change the other.
   */
  const FAMILY_COLOURS = Object.freeze({
    WARNING: '#1a1916',
    SURFACE: '#0369a1',
    TRAIL: '#0f766e',
  });

  /**
   * The family marks as SVG path data on a 24×24 grid, stroked at 2px —
   * the same drawing as templates/includes/_icon_observation_family.html,
   * where circles are spelt as arcs so one Path2D draws each. Change one,
   * change the other.
   */
  const FAMILY_PATHS = Object.freeze({
    WARNING: ['M13 2 9 9l5 3-4 5 2 5', 'M9 9 4 8', 'm14 12 5-1'],
    SURFACE: ['M12 2v20M4.9 7l14.2 10M4.9 17 19.1 7', 'm9 3 3 2 3-2M9 21l3-2 3 2'],
    TRAIL: [
      'M11 7a3 3 0 1 1-6 0a3 3 0 1 1 6 0',
      'M19.5 8a2.5 2.5 0 1 1-5 0a2.5 2.5 0 1 1 5 0',
      'M2 20c0-3.3 2.7-6 6-6s6 2.7 6 6',
      'M14.5 14.3A5 5 0 0 1 22 18.5',
    ],
  });

  /**
   * Shift a YYYY-MM-DD day by whole days, in calendar arithmetic (UTC
   * noon, so no daylight-saving edge can move it a day).
   *
   * @param {string} dayKey
   * @param {number} n
   * @returns {string}
   */
  function addDays(dayKey, n) {
    const d = new Date(`${dayKey}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  /**
   * Which state a report is drawn in, or null when it is outside the
   * window the map's day reads.
   *
   *   - The map shows today: today's reports are `live`, the three days
   *     before are `recent`.
   *   - The map shows another day: that day's reports are `day`, and
   *     nothing else is drawn.
   *
   * YYYY-MM-DD strings compare in calendar order, so no Date is needed.
   *
   * @param {string|null|undefined} observedOn
   * @param {string|null|undefined} displayDate
   * @param {string|null|undefined} today
   * @returns {'live'|'recent'|'day'|null}
   */
  function reportState(observedOn, displayDate, today) {
    if (!observedOn || !today) return null;
    const day = displayDate || today;
    if (day !== today) return observedOn === day ? 'day' : null;
    if (observedOn === today) return 'live';
    if (observedOn < today && observedOn >= addDays(today, -RECENT_DAYS)) return 'recent';
    return null;
  }

  /**
   * The short age a live pin carries: minutes under an hour, then hours.
   * Never "0m" — a report filed this minute reads "1m". An unreadable or
   * future time reads as just filed.
   *
   * @param {string|null|undefined} observedAt
   * @param {number} now - ms since the epoch.
   * @returns {string}
   */
  function ageLabel(observedAt, now) {
    const t = observedAt ? new Date(observedAt).getTime() : NaN;
    const minutes = Number.isFinite(t) ? Math.floor((now - t) / 60000) : 0;
    if (minutes < 60) return `${Math.max(1, minutes)}m`;
    return `${Math.floor(minutes / 60)}h`;
  }

  /**
   * A copy of the collection holding only the reports the map's day
   * draws, each stamped with `_state` and, when live, `_age`. Never
   * mutates its input: the raw payload is what the offline cache keeps,
   * and it has to be re-stamped against whatever day it is read back on.
   *
   * @param {any} geojson
   * @param {string|null|undefined} displayDate
   * @param {string|null|undefined} today
   * @param {number} now - ms since the epoch.
   * @returns {any}
   */
  function withStates(geojson, displayDate, today, now) {
    if (!geojson || !Array.isArray(geojson.features)) return geojson;
    const features = [];
    for (const feature of geojson.features) {
      const props = feature.properties || {};
      const state = reportState(props.observed_on, displayDate, today);
      if (!state) continue;
      const stamped = { ...props, _state: state };
      if (!FAMILIES.includes(stamped.family)) stamped.family = 'WARNING';
      if (state === 'live') stamped._age = ageLabel(props.observed_at, now);
      features.push({ ...feature, properties: stamped });
    }
    return { ...geojson, features };
  }

  /**
   * The URL a fetch for the map's day asks. Today needs no `?d=` — the
   * bare URL is the live-and-recent window, and keeps the one cache entry
   * the offline read-back has always used.
   *
   * @param {string} baseUrl
   * @param {string|null|undefined} displayDate
   * @param {string|null|undefined} today
   * @returns {string}
   */
  function fetchUrl(baseUrl, displayDate, today) {
    if (!displayDate || displayDate === today) return baseUrl;
    const sep = baseUrl.includes('?') ? '&' : '?';
    return `${baseUrl}${sep}d=${encodeURIComponent(displayDate)}`;
  }

  /**
   * A list property as an array. MapLibre hands a feature back from
   * `queryRenderedFeatures` with every non-scalar property serialised to
   * JSON text, so `types` is an array on one path and a string on another.
   *
   * @param {unknown} value
   * @returns {string[]}
   */
  function asList(value) {
    if (Array.isArray(value)) return value.map(String);
    if (typeof value === 'string') {
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed)) return parsed.map(String);
      } catch (_e) {
        return value ? [value] : [];
      }
      return value ? [value] : [];
    }
    return [];
  }

  self.pwaCommunityReportsCore = Object.freeze({
    RECENT_DAYS,
    FAMILIES,
    FAMILY_COLOURS,
    FAMILY_PATHS,
    addDays,
    reportState,
    withStates,
    ageLabel,
    fetchUrl,
    asList,
  });
})();
