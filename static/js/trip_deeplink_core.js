/*
 * static/js/trip_deeplink_core.js — the map's `?trip=` / `?trip_share=`
 * arithmetic (SNOW-828).
 *
 * A trip page's "View on the map" control is a plain link to `/?trip=<uuid>`
 * (for somebody on the trip) or `/?trip_share=<token>` (for somebody holding
 * the link). This module owns everything about that arrival that is a pure
 * function of the query string and the two URL templates the page carries:
 * which parameter is present, where its geometry is fetched from, and what
 * the address bar should say once it has been read. `map.js` owns the
 * fetching, the source, the layers and the camera.
 *
 * ## Two parameters, not one
 *
 * A uuid and an 11-character token are not confusable, so ONE parameter
 * discriminated by the shape of its value would work. It is two because the
 * rule it encodes — a link-holder is never handed a uuid, and the two
 * endpoints scope themselves differently — belongs in the URL space where
 * the rest of the trips app keeps it, not inside a regex here. See
 * `apps/trips/views.py::_map_deep_link`.
 *
 * ## Consumed, never merely read
 *
 * `strip` exists because a parameter left in the address bar is a standing
 * instruction, not a record of how you got here: the map refetches its
 * overlays on several signals, and a `?trip=` still sitting there would fly
 * the camera back to the trip every time. `route_share` is stripped for the
 * same reason (`map.js::consumeRouteShareDeepLink`) — the difference is that
 * a route share ALSO lives in the session, so stripping it loses nothing,
 * whereas a trip is transient by choice and stripping it is the whole of its
 * lifetime. That is deliberate: a trip has a durable page of its own to go
 * back to, which the arrival banner links to.
 *
 * ## Exports (frozen `self.pwaTripDeepLinkCore`)
 *
 *   PARAMS                        — the two parameter names, in read order
 *   read(search)                  → {param, kind, value} or null
 *   endpointFor(link, templates)  → the geojson URL, or null
 *   strip(search)                 → the query string with both removed
 *
 * Every function here is pure, so it is unit-tested directly
 * (tests/js/test_trip_deeplink_core.js) with no browser and no map.
 */

(function () {
  'use strict';

  /**
   * The two parameters, in the order they are read.
   *
   * `trip` first: a viewer who is both on the trip AND holding the link
   * should get the participant-scoped endpoint, which answers for a trip
   * whose share has since been revoked. A URL carrying both is not a shape
   * anything in this project emits, so the order is a tiebreak rather than
   * a contract — but an arbitrary one would be a silent coin-toss.
   *
   * `kind` is what `endpointFor` keys its template choice off, so the
   * parameter name and the identifier space it carries are named together
   * rather than re-derived.
   */
  var PARAMS = Object.freeze([
    Object.freeze({ param: 'trip', kind: 'uuid' }),
    Object.freeze({ param: 'trip_share', kind: 'token' }),
  ]);

  /**
   * The placeholder each identifier space is spelled with, matching
   * `apps.public.views._trips_context`.
   */
  var PLACEHOLDER = Object.freeze({ uuid: '__UUID__', token: '__TOKEN__' });

  /**
   * Read whichever trip parameter a query string carries.
   *
   * @param {string} search A `location.search`, with or without its `?`.
   * @returns {?Object} `{param, kind, value}`, or null when there is none.
   */
  function read(search) {
    if (typeof search !== 'string' || !search) return null;
    var params = new URLSearchParams(search);
    for (var i = 0; i < PARAMS.length; i += 1) {
      var value = params.get(PARAMS[i].param);
      // An empty value is no value: `?trip=` is a malformed link, and
      // fetching `/trips//route.geojson` for it would 404 noisily rather
      // than doing nothing quietly.
      if (value) {
        return { param: PARAMS[i].param, kind: PARAMS[i].kind, value: value };
      }
    }
    return null;
  }

  /**
   * Build the geojson URL for one link from the page's URL templates.
   *
   * The value is percent-encoded into the template rather than
   * concatenated raw. Neither identifier space contains a character that
   * needs it — a uuid is hex and dashes, a token is `token_urlsafe` — but
   * the value arrives from the address bar, so it is attacker-controlled,
   * and a path built from it must not be assemblable into something else.
   *
   * @param {?Object} link A `read()` result.
   * @param {?Object} templates `{uuid, token}` URL templates.
   * @returns {?string} The URL to fetch, or null when either is missing.
   */
  function endpointFor(link, templates) {
    if (!link || !templates) return null;
    var template = templates[link.kind];
    var placeholder = PLACEHOLDER[link.kind];
    if (!template || !placeholder) return null;
    if (template.indexOf(placeholder) === -1) return null;
    return template.replace(placeholder, encodeURIComponent(link.value));
  }

  /**
   * Remove BOTH trip parameters from a query string.
   *
   * Both, not just the one that was read: a URL carrying `?trip` and
   * `?trip_share` together would otherwise keep the unread one, which is
   * the standing instruction this function exists to prevent.
   *
   * @param {string} search A `location.search`, with or without its `?`.
   * @returns {string} The remaining query string, `?`-prefixed, or ''.
   */
  function strip(search) {
    if (typeof search !== 'string' || !search) return '';
    var params = new URLSearchParams(search);
    for (var i = 0; i < PARAMS.length; i += 1) params.delete(PARAMS[i].param);
    var rest = params.toString();
    return rest ? '?' + rest : '';
  }

  self.pwaTripDeepLinkCore = Object.freeze({
    PARAMS: PARAMS,
    read: read,
    endpointFor: endpointFor,
    strip: strip,
  });
})();
