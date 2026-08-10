/*
 * static/js/map_roundel_overlay_state.js — "my overlay is on the map" on the
 * three roundels that switch one (SNOW-658).
 *
 * Downloads, Favourites and Field observations each open a panel whose
 * footer carries a "Display on the map" switch
 * (templates/includes/_map_overlay_toggle.html). Until this module the
 * roundel itself said nothing about that switch's position, so the only way
 * to find out whether your favourites were being drawn was to open the panel
 * and look — and the downloads roundel, the one roundel that DID carry a
 * state, carried a different fact entirely ("this device holds at least one
 * downloaded area"). One roundel, two meanings, no way to tell which you
 * were reading.
 *
 * So: ONE mechanism for all three. Every roundel below carries
 * ``data-overlay-shown="true|false"``, meaning exactly one thing on each —
 * "the overlay this roundel's panel switches is currently drawn on the map"
 * — painted by one function, from one event, into one CSS rule
 * (static/css/map.css, `[data-overlay-shown="true"]`). Three hand-rolled
 * signals is how they diverged in the first place; a fourth roundel joins by
 * adding a line to ROUNDELS.
 *
 * PAINT, not preference. Each bridge's ``isVisible()`` answers from the
 * layers MapLibre is actually drawing (SNOW-658 review), so an overlay the
 * user has switched on but whose data never arrived — offline, first enable,
 * a failed fetch — leaves its ring off, and a placement flow that clears the
 * map down to the basemap takes all three off for its duration. The panel
 * switch inside each roundel's own panel reads the bridge's ``isEnabled()``
 * instead and can therefore read ON while the ring is off: the switch states
 * what was asked for, the ring states what happened. A ring that claimed
 * "shown" over a blank map would be this project's oldest status-indicator
 * defect wearing a new hat.
 *
 * The state is LIVE, not boot-only. ``snowdesk:overlay-visibility-changed``
 * (static/js/map.js's ``announceOverlayVisibility``) fires from every writer
 * of any of the three overlays: the panel switches, the bulletins-exclusivity
 * path that switches the downloaded squares off from the layers menu, the
 * boot seed, the ``styledata`` re-install after a basemap swap, each lazy
 * overlay's install (and the settle of a load that installed nothing), and
 * static/js/map_placement_focus.js. The event carries no detail — this module
 * re-reads all three bridges — so a new writer only has to announce, never to
 * describe.
 *
 * Accessibility: the ring is decorative-but-meaningful, so the state is also
 * in the roundel's own ``aria-label``, which is the idiom these roundels
 * already used (the downloads roundel swapped between two translated labels
 * before this ticket). Not ``aria-pressed``: all three buttons OPEN a panel,
 * and a toggle-button role would announce the panel, not the overlay.
 *
 * LOAD ORDER: needs the three bridges, which map.js publishes at parse time,
 * so its <script> tag sits after the map bundle in home.html. It is not a
 * bundle member — nothing in the bundle reads it. Order is belt-and-braces
 * either way: this module paints once at parse time AND map.js announces
 * once its bridges exist, so whichever runs second corrects the other.
 */

(function mapRoundelOverlayStateInit() {
  'use strict';

  // SNOW-620: server-translated, read back from the template
  // _map_embed.html renders — makemessages does not scan JavaScript. The
  // literal below is the English fallback (see static/js/i18n_strings.js).
  const STRINGS = self.pwaStrings.read('map-roundel-strings-template', {
    'roundel-overlay-shown': '%(label)s — shown on the map',
  });

  /*
   * The three roundels, each with the bridge that answers for its own
   * overlay. Written out one line each rather than resolved from a
   * ``window[name]`` lookup: a literal ``window.pwaFavouritesOverlay`` is
   * what bin/js-globals-lint can check against the tree's assignments, and a
   * dynamic lookup would be invisible to it.
   */
  const ROUNDELS = [
    {
      id: 'map-custom-download-control',
      isVisible: () => !!window.pwaDownloadedOverlay?.isVisible?.(),
    },
    {
      id: 'favourite-add-btn',
      isVisible: () => !!window.pwaFavouritesOverlay?.isVisible?.(),
    },
    {
      id: 'report-btn',
      isVisible: () => !!window.pwaCommunityReportsOverlay?.isVisible?.(),
    },
  ];

  // Each roundel's server-rendered ``aria-label``, captured before this
  // module ever rewrites it — the "off" label, and the stem the "on" one is
  // built from. Read back from the element it would be lost, since by then
  // the attribute may be this module's own composed value.
  const BASE_LABELS = new Map();

  /**
   * The roundel's own untouched label, captured on first sight.
   *
   * @param {HTMLElement} el - One of the three roundel buttons.
   * @returns {string} The server-rendered ``aria-label``, or '' if it has none.
   */
  function baseLabel(el) {
    if (!BASE_LABELS.has(el.id)) {
      BASE_LABELS.set(el.id, el.getAttribute('aria-label') || '');
    }
    return BASE_LABELS.get(el.id);
  }

  /**
   * Paint one roundel from its own overlay's current visibility.
   *
   * The label is composed rather than picked from a pair, so the three
   * roundels keep their own distinct names ("Your favourites", "Manage
   * offline downloads") and only one translated string states the shared
   * fact. ``title`` follows the label where the roundel already has one —
   * the tooltip and the screen-reader text must not disagree — and is not
   * invented for a roundel that never had one.
   *
   * @param {{id: string, isVisible: function(): boolean}} entry - A ROUNDELS row.
   * @returns {void}
   */
  function paint(entry) {
    const el = document.getElementById(entry.id);
    if (!el) return;
    const shown = entry.isVisible();
    const label = baseLabel(el);
    el.dataset.overlayShown = shown ? 'true' : 'false';
    if (!label) return;
    const text = shown
      ? self.pwaStrings.interpolate(STRINGS['roundel-overlay-shown'], { label })
      : label;
    el.setAttribute('aria-label', text);
    if (el.hasAttribute('title')) el.title = text;
  }

  /**
   * Repaint all three roundels from the bridges.
   *
   * Every roundel on every call: the event that drives this deliberately
   * says nothing about WHICH overlay moved, so that a new writer only has
   * to announce. Three DOM writes is not a cost worth a detail payload
   * that can fall out of step.
   *
   * @returns {void}
   */
  function refresh() {
    for (const entry of ROUNDELS) paint(entry);
  }

  document.addEventListener('snowdesk:overlay-visibility-changed', refresh);
  refresh();

  // Frozen bridge, the idiom every other cross-IIFE export here uses.
  // Exposed for the same reason pwaLayerSyncStatus is: a surface that
  // changes an overlay's visibility WITHOUT going through map.js (none
  // today) can settle the roundels rather than waiting for the next
  // announcement.
  window.pwaRoundelOverlayState = Object.freeze({ refresh: refresh });
})();
