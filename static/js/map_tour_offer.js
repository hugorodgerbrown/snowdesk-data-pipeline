/**
 * static/js/map_tour_offer.js — the one-time offer of the map tour (SNOW-1117).
 *
 * Reveals #map-tour-offer, a small card that asks a first-time visitor
 * whether they would like the coachmark tour (static/js/map_help.js). It
 * replaced home_intro.js and the "Welcome to Snowdesk" panel, which opened
 * over the map on load and said what Snowdesk is — the homepage says that
 * now — and which a returning visitor saw flash open before the script hid
 * it. This card is rendered `hidden`, so a first paint never shows it.
 *
 * Behaviour:
 *   - Waits for the map to settle: the page's `load`, then the first
 *     'snowdesk:country-ratings-loaded' (the colours are on the map), capped
 *     at RATINGS_WAIT_MS so a slow or failed feed does not hold it back for
 *     good, then SETTLE_MS more. The map is the first thing seen; the offer
 *     comes after it.
 *   - Offered once per device. Any of these means "already offered":
 *       'snowdesk.map.tour-offer' = 'dismissed' — this card was answered;
 *       'snowdesk.map.help' = 'seen'            — the tour was finished;
 *       'snowdesk.home.intro' = 'dismissed'     — the welcome panel this
 *         replaced was dismissed, and with it its "Explore the map" offer.
 *   - Not offered on a deep link (DEEP_LINK_PARAMS): someone arriving at a
 *     shared route, a trip, a resort or a panel came for that, and the
 *     surface it opens is already over the map. Nor while another surface
 *     is up when the moment comes (an object sheet, the route rail, the tour
 *     itself) — that visit simply goes without, and nothing is recorded.
 *   - "Start the tour" records the answer and dispatches
 *     'snowdesk:map-help-requested', which map_help.js answers by opening the
 *     tour. "Not now", the "×" and Escape record the answer and close.
 *   - It is a map overlay, so it registers with window.pwaMapOverlays
 *     (static/js/map_overlay_exclusivity.js): opening any other overlay
 *     closes it, without recording an answer — being displaced is not the
 *     visitor declining.
 *   - '?tour-offer=1' (or 'true') shows it whatever the stored state, after
 *     the same wait, for QA and screenshots. It is stripped from the address
 *     bar once the card is answered, so a reload does not bring it back.
 *   - prefers-reduced-motion: the card appears without its slide-in.
 */

(function mapTourOfferInit() {
  'use strict';

  const offer = document.getElementById('map-tour-offer');
  if (!offer) return;

  const startBtn = document.getElementById('map-tour-offer-start');
  const laterBtn = document.getElementById('map-tour-offer-later');
  const closeBtn = document.getElementById('map-tour-offer-close');

  const OFFER_KEY = 'snowdesk.map.tour-offer';
  const OFFER_DISMISSED = 'dismissed';
  const TOUR_KEY = 'snowdesk.map.help';
  const TOUR_SEEN = 'seen';
  const LEGACY_INTRO_KEY = 'snowdesk.home.intro';
  const LEGACY_INTRO_DISMISSED = 'dismissed';
  const FORCE_PARAM = 'tour-offer';
  // This surface's name in the shared map-overlay registry — the element's
  // own id, the convention map_help.js set.
  const OVERLAY_NAME = 'map-tour-offer';
  const DEEP_LINK_PARAMS = [
    'route_share', 'trip', 'trip_share', 'favourite', 'resort', 'panel',
    'edit', 'layers',
  ];
  const RATINGS_WAIT_MS = 6000;
  const SETTLE_MS = 1500;

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    offer.classList.add('map-tour-offer--no-motion');
  }

  /**
   * Read a localStorage key, treating a storage error as "not set".
   *
   * @param {string} key
   * @returns {string|null}
   */
  const read = (key) => {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      // Private mode — nothing was stored, so nothing has been answered.
      return null;
    }
  };

  /** Whether the query string asks for the card whatever the stored state. */
  const forced = () => {
    const value = new URLSearchParams(location.search).get(FORCE_PARAM);
    return value !== null && ['1', 'true'].includes(value.toLowerCase());
  };

  /** Whether this device has already been offered the tour, or taken it. */
  const alreadyOffered = () => (
    read(OFFER_KEY) === OFFER_DISMISSED
    || read(TOUR_KEY) === TOUR_SEEN
    || read(LEGACY_INTRO_KEY) === LEGACY_INTRO_DISMISSED
  );

  /** Whether the visit arrived on a link to something in particular. */
  const isDeepLink = () => {
    const params = new URLSearchParams(location.search);
    return DEEP_LINK_PARAMS.some((name) => params.has(name));
  };

  /**
   * Whether another surface holds the screen: an object sheet on a phone,
   * the route rail, or the tour. The offer waits for none of them; it just
   * does not appear this visit.
   */
  const screenIsBusy = () => {
    if (document.documentElement.hasAttribute('data-map-sheet')) return true;
    const map = document.getElementById('map');
    if (map && map.hasAttribute('data-route-rail-open')) return true;
    const tour = document.getElementById('map-help-overlay');
    return Boolean(tour && !tour.hasAttribute('hidden'));
  };

  const isOpen = () => !offer.hasAttribute('hidden');

  const show = () => {
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
    offer.removeAttribute('hidden');
  };

  const hide = () => {
    offer.setAttribute('hidden', '');
  };

  /** Drop '?tour-offer=1' from the address bar, keeping everything else. */
  const stripForceParam = () => {
    const params = new URLSearchParams(location.search);
    if (!params.has(FORCE_PARAM)) return;
    params.delete(FORCE_PARAM);
    const query = params.toString();
    history.replaceState(
      null,
      '',
      location.pathname + (query ? `?${query}` : '') + location.hash,
    );
  };

  /** Close the card and record that the offer was answered. */
  const answer = () => {
    hide();
    try {
      localStorage.setItem(OFFER_KEY, OFFER_DISMISSED);
    } catch (_) {
      // Private mode — the answer holds for this page only.
    }
    stripForceParam();
  };

  if (startBtn) {
    startBtn.addEventListener('click', () => {
      answer();
      document.dispatchEvent(new CustomEvent('snowdesk:map-help-requested'));
    });
  }
  if (laterBtn) laterBtn.addEventListener('click', answer);
  if (closeBtn) closeBtn.addEventListener('click', answer);

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !isOpen()) return;
    answer();
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, {
    isOpen: isOpen,
    close: hide,
  });

  // ---- When to offer ----

  if (!forced() && (alreadyOffered() || isDeepLink())) return;

  let offered = false;
  const offerNow = () => {
    if (offered) return;
    offered = true;
    if (screenIsBusy()) return;
    show();
  };

  const afterLoad = () => {
    let settleTimer = null;
    let capTimer = null;
    const settle = () => {
      if (settleTimer !== null) return;
      clearTimeout(capTimer);
      settleTimer = setTimeout(offerNow, SETTLE_MS);
    };
    capTimer = setTimeout(settle, RATINGS_WAIT_MS);
    document.addEventListener('snowdesk:country-ratings-loaded', settle, {
      once: true,
    });
  };

  if (document.readyState === 'complete') {
    afterLoad();
  } else {
    window.addEventListener('load', afterLoad, { once: true });
  }
})();
