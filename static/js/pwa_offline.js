/*
 * static/js/pwa_offline.js — Connection-state + freshness UI (SNOW-377).
 *
 * Ships spec §3.5, §10.1, §10.2, §10.4, §10.7 non-negotiables:
 *
 *   (1) Permanent connectivity symbol — the ``[data-network-indicator]``
 *       <summary> in ``includes/nav.html``, painted "using the network" or
 *       "not using the network" on every state change. Not using the
 *       network means ``navigator.onLine === false``, OR a fetch just
 *       failed (an ``AbortError`` is a caller cancelling its own request,
 *       not a connectivity failure, so it is excluded), OR the service
 *       worker is in an offline mode.
 *   (2) Freshness on demand — pressing that symbol opens the
 *       connection-status panel (``includes/_connection_panel.html``),
 *       anchored beneath it in the header, which shows how long ago this
 *       device last reached the server, explains the current state, and
 *       offers the way back to the network.
 *   (3) Network-required buttons — any element carrying
 *       ``data-network-required`` is set ``disabled`` when offline and
 *       re-enabled when back online. Non-button elements get an
 *       ``aria-disabled="true"`` + ``pointer-events: none`` fallback.
 *
 * The banner is gone (SNOW-748)
 * ------------------------------
 * (1) and (2) used to be one surface: ``includes/_offline_banner.html``, a
 * full-width strip above the nav that this module revealed when the app was
 * not reaching the server and hid again when it was. It has been deleted.
 * The header now carries a PERMANENT symbol, which is a stronger guarantee
 * than the strip ever gave — the strip said nothing at all while the app was
 * healthy, so a user only ever learned where to look by losing their
 * connection — and the strip's own content (the "last synced" phrase, the
 * per-state explanation, the reconnect button) moved into the panel behind a
 * press on that symbol.
 *
 * The freshness timestamp is therefore one interaction further away than it
 * was. That is deliberate and is recorded as such in docs/offline-first.md:
 * an always-visible state indicator plus a one-press timestamp beats an
 * indicator that only exists in the failure case.
 *
 * Two clocks (SNOW-482)
 * ----------------------
 * The banner conflated two different facts into one in-memory value:
 * when the *device last synced* with the server, and when the *server
 * generated the data* it last returned. These are now tracked and
 * persisted separately:
 *
 *   - ``syncLastAt`` — wall-clock time of the most recent *successful*
 *     (2xx) same-origin response that is neither cache-served
 *     (``X-SW-Cache`` absent) nor a synthesized service-worker fallback
 *     (a non-empty resolved URL — see SNOW-490: an offline cache miss
 *     in ``static/js/sw.js`` resolves to a synthesized 504 with
 *     ``url === ''``, which must not be mistaken for a real
 *     round-trip). Persisted to IndexedDB ``meta:app`` under key
 *     ``sync.last_at``.
 * It is read back from ``meta:app`` on init, before the first
 * ``renderConnectionUi`` call, so a cold offline launch shows the real
 * last-known value rather than resetting to blank.
 *
 * SNOW-615: there was a second clock here, ``freshnessLastGeneratedAt``,
 * tracking the newest ``X-Data-Generated-At`` header. It was declared,
 * assigned on every qualifying response, persisted, and hydrated on every
 * page boot — and never read. The panel has one ``data-role="synced-at"``
 * cell, filled from the clock above; the template's own comment said so
 * while this header claimed both reached the UI. Deleted rather than
 * wired in: nothing had asked for a second timestamp in three tickets'
 * worth of work, and a write-only clock costs a put per response and a
 * read per boot to misdirect the next reader.
 *
 * Qualifying requests — ``/api/*`` and other non-static-asset
 * same-origin responses (HTML partials/navigations included) — also
 * append a row to the ``log:sync`` IndexedDB store (SNOW-482), read
 * out by the manage-page sync-log panel behind the ``sync_log`` waffle
 * flag.
 *
 * Network mode (SNOW-742)
 * ------------------------
 * The UI used to key entirely off ``navigator.onLine`` plus "did a fetch just
 * fail". Neither can see the state this ticket added: the service worker has
 * LATCHED offline — stopped calling the network at all after three
 * consecutive read timeouts — while ``navigator.onLine`` is still true. That
 * combination is the Underground exactly: the radio is attached, so the
 * platform reports online; there is no route, so nothing completes.
 *
 * So the panel has four states rather than one, and they make different
 * promises. "Online — last synced" means the app is using the network.
 * "Offline — last synced" means requests are still going out and the app will
 * update as soon as one lands. "Offline mode — last synced" means it has
 * stopped asking and is serving downloaded data only. Showing the second
 * while the third is true would be a lie about avalanche data, which is why
 * every predicate below tests the mode and not ``onLine`` alone.
 *
 * The worker owns the mode; ``networkMode`` here is a mirror, kept in step by
 * the ``network-mode`` message in both directions. It is persisted to
 * ``meta:app`` under ``network.mode`` and re-asserted to the worker on boot —
 * a worker terminated while idle comes back in ``'auto'`` having forgotten the
 * latch, and re-asserting is what restores it without putting an IndexedDB
 * read on the worker's own fetch path.
 *
 * The symbol and the switch (SNOW-748)
 * -------------------------------------
 * SNOW-742 put the user's own way into offline mode inside the banner, which
 * revealed only once the connection had already failed, so the control for "I
 * have signal and am about to lose it" was unreachable in exactly that case.
 * It moved to ``includes/nav.html``, and the model it follows there is a
 * phone's aeroplane mode: a symbol in the status bar, a switch in the
 * settings. Two elements, both painted by this module (as ``mutation_queue.js``
 * paints the sync badge beside them):
 *
 *   * ``[data-network-indicator]`` — the header symbol. ALWAYS rendered,
 *     never hidden; this module swaps its glyph, its colour, its accessible
 *     name and its ``data-network-state`` between the two states. It is a
 *     disclosure — the ``<summary>`` of a ``<details data-network-panel>``,
 *     like the two dropdowns beside it — so pressing it opens the panel and
 *     pressing it again closes it, and it never changes the network mode.
 *     Opening and closing are the BROWSER's; this module follows the
 *     ``toggle`` event to keep ``aria-expanded`` and the freshness ticker in
 *     step, and nav.html's own script adds outside-click, Escape and the
 *     panel's close control. That is why nothing here binds a click on the
 *     symbol, and why the panel needs no help from ``overlays.js``.
 *   * ``[data-network-toggle]`` — the "Offline mode" row at the top of the
 *     subscriber menu, an ``includes/_switch.html`` checkbox. Revealed here,
 *     its ``checked`` state painted here, its ``change`` event bound here.
 *
 * The two are found independently, and each is optional: the row renders only
 * for a signed-in user, so on an anonymous page the symbol must still be
 * painted with no row present.
 *
 * The two do NOT paint the same predicate, and that is the point. The symbol
 * answers "is this app reaching the server", so a dead interface turns it
 * struck-through even in ``'auto'``. The switch answers "did you ask for
 * offline mode", so a merely-struggling connection must leave it off — a
 * switch that flicks itself on when the lift goes over a ridge is reporting
 * someone else's decision as the user's.
 *
 * That move needed a third mode. The worker's ``'offline'`` is an auto-latch
 * and is probed back to ``'auto'`` within thirty seconds of a route
 * reappearing — correct for a latch, and the exact opposite of what a user who
 * pressed the switch while online asked for. So a user's request is
 * ``'offline-forced'``, which the worker never probes. The three values are
 * ``'auto'``, ``'offline'`` (auto-latched) and ``'offline-forced'`` (the
 * user's), and the comparisons below are NOT interchangeable — see the
 * ``online`` listener in particular.
 *
 * One connectivity answer, not two (SNOW-748)
 * -------------------------------------------
 * The control shipped with a hole underneath it: ``snowdesk:connectivity-changed``
 * carried ``navigator.onLine`` alone and fired only on an interface
 * transition, so forcing offline mode changed nothing anybody downstream could
 * see. The map's layers menu kept its green sync dots and the basemap download
 * controls stayed enabled — the app said it was offline in the header while
 * offering to spend the connection.
 *
 * So the broadcast now carries the EFFECTIVE value (``effectiveOnline()`` —
 * interface up AND ``networkMode === 'auto'``), and it fires on every mode
 * change as well as every interface event. ``window.pwaConnectivity.isOnline()``
 * exposes the same value for the consumers that re-read the state when they
 * repaint, so no surface has to derive it for itself. The worker enforces the
 * matching half — see ``_warmCache``'s forced-mode guard in ``static/js/sw.js``,
 * which refuses a download the UI somehow still dispatches.
 *
 * Every user-facing string for all four states is rendered by
 * ``includes/_connection_panel.html`` and
 * toggled here by ``hidden``, and the symbol's two accessible names by
 * ``includes/nav.html``. Setting any of that text from JavaScript would ship
 * English to every locale — ``makemessages`` never scans ``static/js`` —
 * which is what ``bin/i18n-lint`` fails on. The menu switch needs no string
 * from here at all: its label is fixed ("Offline mode").
 *
 * Deferred to SNOW-375 / follow-ups:
 *   * Pull-to-refresh explicit-network path.
 *   * "Updated HH:MM" post-refresh toast.
 */

(function () {
  'use strict';

  // SNOW-748: the connection-status panel, disclosed by the header symbol.
  // Replaces the ``pwa-offline-banner`` strip this module used to reveal, and
  // the bottom-centred toast that briefly stood in for it.
  const PANEL_ID = 'pwa-connection-panel';
  // The <details> the panel and the symbol live in (``includes/nav.html``).
  // It owns open/closed; this module only reads it and follows its ``toggle``.
  const PANEL_SELECTOR = '[data-network-panel]';
  // The panel's one CTA — the way back to the network.
  const PANEL_CTA_SELECTOR = `#${PANEL_ID} [data-network-reconnect]`;
  const NETWORK_ATTR = 'data-network-required';

  // SNOW-742: the meta:app key the network mode is persisted under, and the
  // mode itself as this page last heard it from the worker.
  //
  // The worker owns the mode; this is a mirror, for two jobs. It decides which
  // panel variant to show, and it is re-asserted to the worker on boot — a
  // worker that was terminated while idle comes back in 'auto' with no memory
  // of the latch, and re-asserting is what restores it.
  //
  // SNOW-748: that re-assert is now the FAST path rather than the only one.
  // The worker recovers a user-FORCED mode from this same row by itself (see
  // sw.js's ``_hydrateNetworkMode``), because a worker recycled with no page
  // open had nothing to push it and silently went back on the network. An
  // auto-latch is still restored from here alone, and deliberately — see that
  // function for why a stale latch is not worth reinstating.
  //
  // SNOW-748: three values, not two — ``'auto'``, ``'offline'`` (the worker
  // latched itself after three read timeouts) and ``'offline-forced'`` (the
  // user switched on "Offline mode" in the account menu). Only the middle one
  // is ever probed, which
  // is why the user's choice survives being online.
  const NETWORK_MODE_KEY = 'network.mode';
  let networkMode = 'auto';

  // SNOW-748: the two nav surfaces in includes/nav.html — the header symbol
  // (every viewer) and the "Offline mode" row in the subscriber menu
  // (signed-in only). Looked up separately on every paint, because whether
  // either exists depends on the page and on who is reading it.
  const NETWORK_INDICATOR_SELECTOR = '[data-network-indicator]';
  const NETWORK_TOGGLE_SELECTOR = '[data-network-toggle]';
  // The switch input inside that row. ``includes/_switch.html`` renders only
  // an ``id`` — SNOW-645 removed its raw attribute passthrough as a SAST
  // finding — so its callers select it by id, as the downloads sheet does.
  const NETWORK_SWITCH_ID = 'nav-offline-mode';

  // SNOW-482: the meta:app key the last-sync clock is persisted under.
  // A sibling ``freshness.last_generated_at`` key went with the write-only
  // clock SNOW-615 removed. Rows already written under it on devices in
  // the field are simply never read again — harmless, and cheaper than a
  // migration to delete one small row.
  const SYNC_LAST_AT_KEY = 'sync.last_at';

  // SNOW-482: cadence at which the panel re-renders its relative
  // "last synced" phrase while OPEN, so it counts up live rather than
  // freezing at the value captured when the user pressed the symbol.
  const FRESHNESS_TICK_MS = 30000;

  // Extensions treated as static-asset requests for the purposes of the
  // sync log — mirrors static/js/sw.js's STATIC_SHELL_EXTENSIONS. These
  // never represent a meaningful "sync" from the user's point of view.
  const STATIC_ASSET_EXTENSIONS = new Set([
    '.css',
    '.js',
    '.svg',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.ico',
    '.woff',
    '.woff2',
    '.webmanifest',
  ]);

  // Same-origin paths that are the page talking to itself rather than
  // fetching anything the user asked for, so they never belong in the
  // sync log. static/js/telemetry.js flushes its buffer on a 30s cadence
  // and on every lifecycle event, which swamped the panel with rows
  // describing nothing the reader did. Listed with and without the
  // trailing slash because the receiver is mounted without one and
  // APPEND_SLASH can resolve either spelling.
  const SYNC_LOG_EXCLUDED_PATHS = new Set(['/api/telemetry', '/api/telemetry/']);

  // In-memory copy of the last-sync clock, hydrated from IndexedDB on
  // init (see hydratePersistedClocks) and kept in sync with the persisted
  // copy on every qualifying response.
  let syncLastAt = null;

  /**
   * Coerce a Date / ISO string to a valid Date, or null on failure so
   * callers can fall back rather than render "Invalid Date".
   *
   * @param {string | Date | null} value
   * @returns {Date | null}
   */
  function toDate(value) {
    if (!value) return null;
    const d = value instanceof Date ? value : new Date(value);
    return Number.isNaN(d.valueOf()) ? null : d;
  }

  /**
   * Format the sync clock as a naturalistic relative phrase — e.g.
   * "6 minutes ago", "2 hours ago", "now" — using the largest sensible
   * unit. Locale-aware via ``Intl.RelativeTimeFormat`` so it localises
   * for free once the UI gains other languages.
   *
   * @param {string | Date | null} value
   * @returns {string}
   */
  function formatRelative(value) {
    const d = toDate(value);
    if (!d) return '';
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
    const diffSeconds = Math.round((d.getTime() - Date.now()) / 1000);
    const units = [
      ['day', 86400],
      ['hour', 3600],
      ['minute', 60],
    ];
    for (const [unit, secs] of units) {
      if (Math.abs(diffSeconds) >= secs) {
        return rtf.format(Math.round(diffSeconds / secs), unit);
      }
    }
    return rtf.format(Math.round(diffSeconds), 'second');
  }

  /**
   * Show or hide every element carrying ``data-role="<role>"``.
   *
   * Document-scoped rather than scoped to one root, because SNOW-748 split
   * the roles across two surfaces — the panel owns the messages, the
   * explanations and the CTA's two labels, the header symbol owns its glyph
   * pair and its two accessible names — and both are painted from the same
   * state in the same pass. Each role is unique in the document.
   *
   * @param {string} role
   * @param {boolean} shown
   * @returns {void}
   */
  function toggleRole(role, shown) {
    document.querySelectorAll(`[data-role="${role}"]`).forEach((el) => {
      el.classList.toggle('hidden', !shown);
    });
  }

  /**
   * Fill the panel's "last synced" span with the sync clock as a relative
   * phrase, degrading to an em dash until the first sync is known.
   *
   * @returns {void}
   */
  function renderFreshnessCells() {
    const syncedCell = document.querySelector('[data-role="synced-at"]');
    if (syncedCell) syncedCell.textContent = formatRelative(syncLastAt) || '—';
  }

  // SNOW-482: re-render the "last synced" phrase on a timer while the panel
  // is OPEN, so the phrase counts up ("6 minutes ago" → "7 minutes ago")
  // rather than freezing at the value it had when the symbol was pressed.
  // Started and cleared from the <details>'s ``toggle`` event, which fires
  // however the panel was closed — the symbol, the "×", Escape or a click
  // outside — and it also self-clears if it wakes to find the panel gone.
  let freshnessTicker = null;

  /**
   * Start the freshness re-render timer, if not already running.
   *
   * @returns {void}
   */
  function startFreshnessTicker() {
    if (freshnessTicker !== null) return;
    freshnessTicker = window.setInterval(() => {
      if (!panelIsOpen()) {
        stopFreshnessTicker();
        return;
      }
      renderFreshnessCells();
    }, FRESHNESS_TICK_MS);
  }

  /**
   * Stop the freshness re-render timer, if running.
   *
   * @returns {void}
   */
  function stopFreshnessTicker() {
    if (freshnessTicker === null) return;
    window.clearInterval(freshnessTicker);
    freshnessTicker = null;
  }

  /**
   * The <details> carrying the symbol and its panel, or null on a page that
   * renders neither.
   *
   * @returns {HTMLDetailsElement|null}
   */
  function panelDisclosure() {
    return document.querySelector(PANEL_SELECTOR);
  }

  /**
   * Whether the connection-status panel is currently open.
   *
   * Read from the <details>, which is the only thing that knows: the panel
   * opens and closes natively, and this module never toggles it.
   *
   * @returns {boolean}
   */
  function panelIsOpen() {
    const disclosure = panelDisclosure();
    return !!disclosure && disclosure.open;
  }

  /**
   * Follow the disclosure's own open/closed state: paint the symbol's
   * ``aria-expanded``, fill the freshness cell, and run the re-render ticker
   * only while the panel is on screen.
   *
   * Called from the ``toggle`` event, so it lands however the panel was
   * closed — the symbol again, the "×", Escape, or a click outside — without
   * this module having to know about any of those mechanisms.
   *
   * @returns {void}
   */
  function syncPanelState() {
    const open = panelIsOpen();
    const indicator = document.querySelector(NETWORK_INDICATOR_SELECTOR);
    if (indicator) indicator.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      renderFreshnessCells();
      startFreshnessTicker();
    } else {
      stopFreshnessTicker();
    }
  }

  /**
   * Repaint every surface this module owns for the current connection state.
   * Idempotent — safe to call on every online/offline transition, every mode
   * change and every qualifying response.
   *
   * ``online`` is "is the app reaching the server", which is NOT
   * ``navigator.onLine``: a fetch that has just failed passes ``false`` while
   * the platform still reports online, because a radio attached to no route
   * reports online throughout (the Underground). The offline modes are folded
   * in here rather than at each call site.
   *
   * The panel is repainted whether or not it is open, so pressing the symbol
   * never shows a frame of the previous state's copy. Its VISIBILITY is not
   * touched: the <details> opens and closes on the user's press alone.
   *
   * @param {boolean} online
   * @returns {void}
   */
  function renderConnectionUi(online) {
    const reaching = online && networkMode === 'auto';
    renderNetworkUi(reaching);
    renderNetworkCopy(reaching);
    if (panelIsOpen()) renderFreshnessCells();
  }

  /**
   * SNOW-742/748: show the message, explanation and CTA label that match the
   * current state. Every variant of each is rendered server-side by
   * ``includes/_connection_panel.html`` and
   * toggled here, so no user-facing string is ever built in JavaScript
   * (docs/i18n.md).
   *
   * Four states. ``reaching`` distinguishes the first two — the app is using
   * the network, or it is trying and failing — and the mode distinguishes the
   * last two, which agree on more than they differ on. Both offline modes
   * share the summary line: it answers "is this app contacting the server",
   * and the answer is no either way. They share the CTA, because the way out
   * is the same. They do NOT share the explanation: the latched copy asserts
   * there is no usable connection, which is exactly what is false when the
   * user chose the mode while online.
   *
   * @param {boolean} reaching — the app is using the network.
   * @returns {void}
   */
  function renderNetworkCopy(reaching) {
    const auto = networkMode === 'auto';
    const forced = networkMode === 'offline-forced';
    const latched = networkMode === 'offline';
    toggleRole('online-message', auto && reaching);
    toggleRole('offline-message', auto && !reaching);
    toggleRole('latched-message', !auto);
    toggleRole('online-explainer', auto && reaching);
    toggleRole('offline-explainer', auto && !reaching);
    toggleRole('latched-explainer', latched);
    toggleRole('forced-explainer', forced);
    // Same button, two labels: "try reconnecting" reads as a repair, which is
    // the wrong promise when nothing is broken and the user simply chose this.
    toggleRole('reconnect-label', latched);
    toggleRole('resume-label', forced);
    // The way back to normal operation, offered only where it does something
    // — under either offline mode, and not while the app is merely struggling
    // and still trying on its own, nor while it is succeeding.
    const cta = document.querySelector(PANEL_CTA_SELECTOR);
    if (cta) cta.classList.toggle('hidden', auto);
  }

  /**
   * SNOW-748: paint the two nav surfaces this mode owns
   * (``includes/nav.html``).
   *
   * The header symbol is PERMANENT — it is never hidden, and this function
   * only ever changes what it says. That is the change SNOW-748's rework
   * made: an earlier pass hid it in ``'auto'``, on the phone's-aeroplane-glyph
   * model, which meant the one element telling a user whether their avalanche
   * data was live existed only once it was not. Two glyphs, two colours and
   * two accessible names are rendered in the template and swapped here; the
   * same bit is written to ``data-network-state`` so a reader (or a test) can
   * ask the symbol what it is showing.
   *
   * The menu switch is revealed here rather than rendered visible, the same
   * contract ``mutation_queue.js`` has with the sync badge beside it: a
   * control that only works because a script is running must not be on screen
   * when that script is not.
   *
   * The two paint DIFFERENT predicates. The symbol takes ``reaching`` — a
   * dead interface strikes it through even in ``'auto'``, which is the whole
   * point of a permanent indicator. The switch takes the MODE alone: it is
   * the user's own setting, and flicking itself on because a request timed
   * out would report the worker's decision as theirs.
   *
   * Each element is guarded on its own, not behind a shared early return: the
   * row renders only for a signed-in user, so an anonymous page has the symbol
   * and no row, and the symbol must still be painted.
   *
   * @param {boolean} reaching — the app is using the network.
   * @returns {void}
   */
  function renderNetworkUi(reaching) {
    const indicator = document.querySelector(NETWORK_INDICATOR_SELECTOR);
    if (indicator) indicator.setAttribute('data-network-state', reaching ? 'online' : 'offline');
    toggleRole('network-online-icon', reaching);
    toggleRole('network-offline-icon', !reaching);
    toggleRole('network-name-online', reaching);
    toggleRole('network-name-offline', !reaching);
    const row = document.querySelector(NETWORK_TOGGLE_SELECTOR);
    if (row) {
      row.classList.remove('hidden');
      row.classList.add('flex');
    }
    const input = document.getElementById(NETWORK_SWITCH_ID);
    // ``checked`` on a real checkbox, not an ``aria-checked`` attribute on a
    // button: ``includes/_switch.html`` is an <input role="switch">, so the
    // announced state and the drawn track both follow the property for free.
    // Assigning it fires no ``change`` event, so this cannot loop back into
    // the handler that called it.
    if (input) input.checked = networkMode !== 'auto';
  }

  /**
   * SNOW-748: narrow an arbitrary value to one of the three known modes.
   *
   * Used on both inbound paths — the worker's announcement and the persisted
   * ``meta:app`` row — because both can carry a value written by a different
   * version of the code than the one reading it: a shell cached before this
   * ticket, or a row written by one after it. An unrecognised value becomes
   * ``'auto'``, which is the only mode that claims nothing.
   *
   * @param {*} value
   * @returns {'auto'|'offline'|'offline-forced'}
   */
  function coerceNetworkMode(value) {
    if (value === 'offline' || value === 'offline-forced') return value;
    return 'auto';
  }

  /**
   * Ask the service worker to change mode, and persist the request so a
   * restarted worker can be told about it again on the next boot.
   *
   * @param {'auto'|'offline'|'offline-forced'} mode
   */
  function requestNetworkMode(mode) {
    networkMode = mode;
    persistMeta(NETWORK_MODE_KEY, mode);
    renderConnectionUi(navigator.onLine);
    // SNOW-748: the page's own mode changes are broadcast too, not just the
    // worker's. The toggle's click lands here, and it must not wait for the
    // worker's echo — that echo needs a controller, and a page loaded before
    // the worker activated has none.
    syncNetworkRequired(effectiveOnline());
    broadcastConnectivity();
    try {
      navigator.serviceWorker?.controller?.postMessage({ type: 'network-mode', mode });
    } catch (_err) {
      // No controller yet (first load before activation), or messaging
      // unavailable. The persisted row is re-asserted on the next boot, so the
      // user's choice is not lost — it just takes effect a load later.
    }
  }

  /**
   * Follow the panel's disclosure, bind its CTA and the menu's "Offline mode"
   * switch; then listen for the worker announcing a mode change it made on
   * its own (the latch tripping, or a probe finding a route again).
   */
  function bindNetworkModeControls() {
    // SNOW-748: the header symbol is the <summary> of a native disclosure, so
    // the press that opens and closes the panel is the browser's and nothing
    // is bound to it here — which is also what stops this module ever
    // changing the network mode from the status area, the misread an earlier
    // pass invited and ``aria-expanded`` (rather than ``aria-pressed``)
    // promises against.
    //
    // ``toggle`` is the one event every close arrives on: the symbol, the
    // panel's "×", Escape and a click outside (the last three from nav.html's
    // shared disclosure script) all set ``open``, and this fires for each.
    panelDisclosure()?.addEventListener('toggle', () => {
      syncPanelState();
    });
    // The way back to the network, and the only one an anonymous reader has:
    // the "Offline mode" switch below lives in the subscriber menu, so a
    // signed-out user who gets auto-latched can leave that state only here.
    document.querySelector(PANEL_CTA_SELECTOR)?.addEventListener('click', () => {
      requestNetworkMode('auto');
    });
    // SNOW-748: the menu's "Offline mode" switch. Its two directions are not
    // symmetrical — going offline asks for ``'offline-forced'`` (a choice,
    // never probed), while coming back always asks for plain ``'auto'``
    // whichever offline mode it is leaving, because "use the network again"
    // means the same thing either way.
    //
    // ``change``, not ``click``: the control is a real checkbox, so a keyboard
    // Space and a click on the <label> both arrive here, and the browser has
    // already flipped ``checked`` by the time it does.
    document.getElementById(NETWORK_SWITCH_ID)?.addEventListener('change', (event) => {
      requestNetworkMode(event.target.checked ? 'offline-forced' : 'auto');
    });
    // ``navigator.serviceWorker``'s message queue is disabled until something
    // enables it — setting ``onmessage``, or calling this. An
    // ``addEventListener`` listener alone does NOT enable it, so a message the
    // worker posts before the queue opens is simply never delivered. In
    // practice the queue opens at document load and sw_register.js has always
    // relied on that, but the latch can trip during the initial tile burst,
    // which is close enough to that boundary to be worth removing the
    // question. Idempotent, and a no-op once already enabled.
    try {
      navigator.serviceWorker?.startMessages?.();
    } catch (_err) {
      // Not available (or no container at all) — fall back to the implicit
      // load-time enablement, which is what the rest of the app already uses.
    }
    navigator.serviceWorker?.addEventListener('message', (event) => {
      if (!event.data || event.data.type !== 'network-mode') return;
      // SNOW-748: coerced to the three known values rather than trusted, so a
      // message from an older or newer worker cannot put an unrenderable
      // string in the mirror. Anything unrecognised means 'auto' — the mode
      // that promises least.
      networkMode = coerceNetworkMode(event.data.mode);
      persistMeta(NETWORK_MODE_KEY, networkMode);
      renderConnectionUi(navigator.onLine);
      syncNetworkRequired(effectiveOnline());
      // SNOW-748: a mode change IS a connectivity change for everything that
      // gates on the broadcast — the worker latching, a probe lifting it, or
      // the user pressing the toggle all change whether the network is in
      // use. Without this the event only ever fired on an interface
      // transition, so a forced mode left every consumer believing the
      // network was there.
      broadcastConnectivity();
    });
  }

  /**
   * Toggle the disabled state of every element carrying
   * ``data-network-required``. Direct matches use the native
   * ``disabled`` property where available; matched containers (forms)
   * cascade the disabled state to their submit-capable children so a
   * ``<form data-network-required>`` blocks its Submit / Add / Delete
   * buttons without having to tag each one individually.
   *
   * Non-disable-able elements (``<a>``, ``<div>``) fall back to
   * ``aria-disabled="true"`` + ``pointer-events: none``.
   *
   * @param {boolean} online
   */
  function syncNetworkRequired(online) {
    const nodes = document.querySelectorAll(`[${NETWORK_ATTR}]`);
    nodes.forEach((node) => applyDisabled(node, !online));
    document
      .querySelectorAll(
        `[${NETWORK_ATTR}] button, [${NETWORK_ATTR}] input[type="submit"], [${NETWORK_ATTR}] input[type="button"]`,
      )
      .forEach((child) => applyDisabled(child, !online));
  }

  /**
   * Apply / remove the disabled state on a single element without
   * clobbering a pre-existing ``disabled`` attribute set for other
   * reasons (form validity, etc.). We tag our own mutations with
   * ``data-was-disabled-offline`` so the reverse transition only
   * un-disables what we disabled.
   *
   * @param {Element} node
   * @param {boolean} disabled
   */
  function applyDisabled(node, disabled) {
    if (disabled) {
      if ('disabled' in node && !node.disabled) {
        node.disabled = true;
        node.setAttribute('data-was-disabled-offline', '1');
      }
      node.setAttribute('aria-disabled', 'true');
      node.style.pointerEvents = 'none';
    } else {
      if (
        'disabled' in node &&
        node.getAttribute('data-was-disabled-offline') === '1'
      ) {
        node.disabled = false;
      }
      node.removeAttribute('aria-disabled');
      node.style.removeProperty('pointer-events');
      node.removeAttribute('data-was-disabled-offline');
    }
  }

  /**
   * Best-effort persistence of one ``meta:app`` key. Never throws —
   * IndexedDB unavailability (private mode, Reset Required) must not
   * break the in-memory state update.
   *
   * @param {string} key
   * @param {string} value
   */
  function persistMeta(key, value) {
    if (!window.pwaDb || typeof window.pwaDb.put !== 'function') return;
    try {
      window.pwaDb.put('meta:app', { key, value }).catch(() => {});
    } catch (_err) {
      // Ignore — persistence is best-effort.
    }
  }

  /**
   * Whether a same-origin pathname is a static-asset request that
   * should never be recorded as a "sync" (CSS/JS/images/fonts/the
   * webmanifest, and anything under ``/static/``).
   *
   * @param {string} pathname
   * @returns {boolean}
   */
  function isStaticAssetPath(pathname) {
    if (pathname.startsWith('/static/')) return true;
    const dot = pathname.lastIndexOf('.');
    if (dot === -1) return false;
    return STATIC_ASSET_EXTENSIONS.has(pathname.slice(dot).toLowerCase());
  }

  /**
   * Whether a same-origin pathname is worth recording as a "sync" — a
   * request the reader would recognise as the app fetching something,
   * rather than a static asset or the page's own background chatter.
   *
   * @param {string} pathname
   * @returns {boolean}
   */
  function isLoggableSyncPath(pathname) {
    if (SYNC_LOG_EXCLUDED_PATHS.has(pathname)) return false;
    return !isStaticAssetPath(pathname);
  }

  /**
   * Best-effort append to the ``log:sync`` IndexedDB store. Never
   * throws.
   *
   * @param {Date} at
   * @param {string} pathname
   */
  function appendSyncLogEntry(at, pathname) {
    if (!window.pwaDb || typeof window.pwaDb.appendSyncLog !== 'function') return;
    try {
      window.pwaDb.appendSyncLog({ at: at.toISOString(), path: pathname }).catch(() => {});
    } catch (_err) {
      // Ignore — the sync log is a diagnostic nice-to-have.
    }
  }

  /**
   * Called whenever a response header set + resolved URL + status is
   * available (fetch or HTMX). ``syncLastAt`` / ``sync.last_at`` /
   * the ``log:sync`` row (for paths ``isLoggableSyncPath`` accepts) only
   * advance for a response that is: successful (2xx status), un-stamped
   * (no ``X-SW-Cache`` header — i.e. neither a Cache-Storage replay
   * (``X-SW-Cache: hit``) nor a synthesized offline fallback
   * (``X-SW-Cache: miss``) served by ``static/js/sw.js``), and
   * same-origin with a *non-empty* resolved URL. The empty-URL check matters because a synthesized
   * service-worker fallback (offline cache miss — see SNOW-490) has
   * ``url === ''``, which would otherwise resolve against
   * ``location.href`` and be misclassified as a same-origin success.
   * ``X-Data-Generated-At`` is always absorbed regardless of cache-hit
   * status — a cache-served response still tells the user how old the
   * data it's showing is.
   *
   * That same qualifying-response condition doubles as the symbol's
   * ``online``-independent recovery path — see the call to
   * ``renderConnectionUi(true)`` below.
   *
   * @param {(name: string) => string | null} getHeader
   * @param {string} responseUrl
   * @param {number} status
   */
  function absorbFreshness(getHeader, responseUrl, status) {
    const now = new Date();
    const cacheHit = !!getHeader('X-SW-Cache');
    const successful = status >= 200 && status < 300;

    let sameOrigin = false;
    let pathname = '';
    if (responseUrl) {
      try {
        const parsed = new URL(responseUrl, window.location.href);
        sameOrigin = parsed.origin === window.location.origin;
        pathname = parsed.pathname;
      } catch (_err) {
        sameOrigin = false;
      }
    }

    if (successful && !cacheHit && sameOrigin) {
      syncLastAt = now;
      persistMeta(SYNC_LAST_AT_KEY, now.toISOString());
      if (isLoggableSyncPath(pathname)) {
        appendSyncLogEntry(now, pathname);
      }
      // A real same-origin round-trip proves the connection works, so it
      // is also the symbol's recovery path. Without this the only way back
      // is the ``online`` event, which never fires when connectivity never
      // actually changed — a single failed request on an online page would
      // pin the symbol struck-through for the life of that page.
      if (navigator.onLine) renderConnectionUi(true);
    }

    // Repaint while offline so an open panel's clock updates live when a
    // fresh(er) response arrives, and so the symbol keeps reporting the
    // failure a successful response has not yet cleared.
    if (!navigator.onLine) renderConnectionUi(false);
  }

  /**
   * Wrap ``window.fetch`` so every response participates in the
   * freshness ledger and every network failure flips the symbol. An
   * ``AbortError`` is excluded — see the ``catch`` below.
   */
  function wrapFetch() {
    if (typeof window.fetch !== 'function') return;
    const original = window.fetch.bind(window);
    window.fetch = async function (...args) {
      try {
        const response = await original(...args);
        try {
          absorbFreshness(
            (name) => response.headers.get(name),
            response.url,
            response.status,
          );
        } catch (_err) {
          // Never break the caller on a header quirk.
        }
        return response;
      } catch (err) {
        // An abort is the caller cancelling its own request, not a
        // connectivity failure, so it must not strike the symbol through.
        // The sign-in page hits this on every visit: the WebAuthn
        // conditional ceremony starts on email-input focus
        // (static/js/passkey.js) and is aborted on the first keystroke,
        // which on a fully-online page would otherwise leave the symbol
        // reporting offline — nothing would fire ``online`` to clear it.
        if (err && err.name === 'AbortError') throw err;
        // Network failure — repaint as offline. Rethrow so callers can
        // still handle the failure themselves.
        renderConnectionUi(false);
        throw err;
      }
    };
  }

  /**
   * Hook the HTMX post-response event so XHR-driven traffic also feeds
   * the freshness ledger, and the ``htmx:sendError`` event so a failed
   * mutation reaches the symbol immediately.
   */
  function wrapHtmx() {
    document.body?.addEventListener('htmx:afterOnLoad', (evt) => {
      const xhr = evt?.detail?.xhr;
      if (!xhr || typeof xhr.getResponseHeader !== 'function') return;
      try {
        absorbFreshness(
          (name) => xhr.getResponseHeader(name),
          xhr.responseURL || '',
          xhr.status,
        );
      } catch (_err) {
        // Ignore.
      }
    });
    // No ``AbortError`` guard needed here, unlike the fetch wrapper:
    // htmx raises ``htmx:sendError`` from ``xhr.onerror`` only, and
    // routes a cancelled request to a separate ``htmx:sendAbort`` event
    // we do not listen for. The recovery path is the ``afterOnLoad``
    // handler above, whose ``absorbFreshness`` call repaints as online
    // on the next successful same-origin response.
    document.body?.addEventListener('htmx:sendError', () => {
      renderConnectionUi(false);
    });
  }

  /**
   * SNOW-748: whether the app is using the network AT ALL — the interface is
   * up *and* no offline mode is in force.
   *
   * This is the question every consumer of the broadcast below was really
   * asking, and until this ticket ``navigator.onLine`` was the only answer
   * available to them. It is the wrong one under a forced mode by
   * construction: the user presses the toggle precisely when they have a
   * connection and do not want it spent, so ``onLine`` stays true while the
   * worker refuses every read. The layers menu went on painting green dots and
   * the basemap download controls went on offering downloads the worker would
   * (SNOW-748) refuse.
   *
   * @returns {boolean}
   */
  function effectiveOnline() {
    return navigator.onLine !== false && networkMode === 'auto';
  }

  /**
   * Broadcast the connection state to any listener that needs to react
   * beyond the blunt ``data-network-required`` disable — chiefly the map's
   * layers menu (map_layer_sync_status.js), which gates each row against
   * *cache* state (offline + uncached ⟹ disabled + red dot) rather than
   * disabling everything wholesale, and the basemap download controls. A
   * single event keeps every consumer off its own ``navigator.onLine`` poll
   * and in lockstep with the header symbol.
   *
   * SNOW-748: takes no argument. It carries ``effectiveOnline()``, and the
   * callers that used to pass a literal ``true``/``false`` were the bug — the
   * ``online`` listener passing ``true`` re-enabled the whole UI the first
   * time the radio blinked under a mode the user had chosen. There is one
   * answer to "is this app using the network", and it is computed here rather
   * than at four call sites that can each get it wrong.
   */
  function broadcastConnectivity() {
    document.dispatchEvent(
      new CustomEvent('snowdesk:connectivity-changed', {
        detail: { online: effectiveOnline() },
      }),
    );
  }

  /**
   * Bind ``online`` / ``offline`` events on window so the symbol, the panel
   * and network-required elements track the connection state without
   * requiring a page reload.
   */
  function bindConnectionEvents() {
    window.addEventListener('online', () => {
      // SNOW-742: an interface coming back is the strongest signal there is
      // that the latch should lift, and it beats waiting out the worker's own
      // backoff (up to five minutes). If the route is still dead, three
      // bounded reads re-latch within about nine seconds.
      //
      // SNOW-748: ``=== 'offline'`` EXACTLY, and this is the single most
      // important comparison in the file. An auto-latch is the worker's guess
      // that there is no route, and an ``online`` event is better evidence, so
      // lifting it is right. A forced mode is not a guess about the network at
      // all — the user chose it, very often while online — so an interface
      // event must not overrule it. Widening this to ``!== 'auto'`` would undo
      // the user's choice the first time the radio blinked, which is the bug
      // this ticket exists to fix, moved one file across.
      if (networkMode === 'offline') requestNetworkMode('auto');
      renderConnectionUi(true);
      // SNOW-748: the effective value, not a literal ``true``. Under a forced
      // mode (which the branch above deliberately leaves alone) the network is
      // still not being used, and telling the app otherwise here is what let
      // the download controls come back to life the moment the radio blinked.
      syncNetworkRequired(effectiveOnline());
      broadcastConnectivity();
    });
    window.addEventListener('offline', () => {
      renderConnectionUi(false);
      syncNetworkRequired(false);
      broadcastConnectivity();
    });
  }

  /**
   * SNOW-482: read the persisted clock back from ``meta:app`` before
   * the first ``renderConnectionUi`` call, so a cold offline launch shows the
   * real last-known value instead of resetting to blank. Guarded on
   * ``window.pwaDb`` presence (it loads before this script — see
   * ``base.html``) and never throws — a read failure just leaves the
   * clocks unset, same as before this ticket.
   */
  async function hydratePersistedClocks() {
    if (!window.pwaDb || typeof window.pwaDb.get !== 'function') return;
    try {
      const syncRow = await window.pwaDb.get('meta:app', SYNC_LAST_AT_KEY);
      if (syncRow && syncRow.value) {
        const parsed = new Date(syncRow.value);
        if (!Number.isNaN(parsed.valueOf())) syncLastAt = parsed;
      }
    } catch (_err) {
      // Best-effort — the panel falls back to "no data yet" copy.
    }
    // SNOW-742: and the network mode, which is re-asserted to the worker by
    // ``init`` below. Read separately from the clock above so one failing row
    // can't cost the other.
    //
    // SNOW-748: WHICH offline mode is preserved, not just the fact of one. A
    // forced mode read back as a latch would be probed away within thirty
    // seconds of the next page load — the user's choice surviving a reload is
    // most of what makes it a setting rather than a gesture.
    try {
      const modeRow = await window.pwaDb.get('meta:app', NETWORK_MODE_KEY);
      if (modeRow) networkMode = coerceNetworkMode(modeRow.value);
    } catch (_err) {
      // Best-effort — an unread mode simply starts in 'auto', and the latch
      // re-trips within about nine seconds if the radio really is dead.
    }
  }

  /**
   * Prime the initial state. If the page loaded while offline (unlikely
   * via the browser — offline navigations normally show the browser's
   * own error page — but possible via the SW cache), we want the symbol
   * painted offline immediately, and the panel holding the persisted clock
   * rather than a blank the moment the user presses it.
   */
  async function init() {
    bindConnectionEvents();
    wrapFetch();
    wrapHtmx();
    // SNOW-742: bound BEFORE the IndexedDB read below, not after. The worker
    // can latch during the page's own initial request burst, and a listener
    // attached behind an await would miss the announcement — leaving the app
    // latched with the panel still claiming it is merely struggling.
    bindNetworkModeControls();
    await hydratePersistedClocks();
    // SNOW-742: re-assert the persisted mode to the worker. A worker
    // terminated while idle comes back in 'auto' having forgotten the latch;
    // this is what restores it, and it is why the worker never has to read
    // IndexedDB on its own fetch path.
    //
    // SNOW-748: either offline mode is re-asserted, as ITSELF. Sending
    // 'offline' for a forced mode would hand the worker a latch, which
    // schedules the probe that ends it.
    if (networkMode !== 'auto') requestNetworkMode(networkMode);
    renderConnectionUi(navigator.onLine);
    syncNetworkRequired(effectiveOnline());
    // Prime consumers with the initial state so a page that loaded offline
    // (via the SW cache), or one that booted straight back into a persisted
    // forced mode, gets its cache-aware gating applied at boot rather than
    // only on the next transition.
    broadcastConnectivity();
  }

  // SNOW-748: the READ half of the broadcast above, for the consumers that
  // re-render on ``snowdesk:connectivity-changed`` but then decide what to
  // paint by reading ``navigator.onLine`` again — the two basemap download
  // controls, the downloads sheet and the layers menu's sync dots. The event
  // is still what tells them to re-render; this is what they ask when they
  // do, so the answer is the same one the symbol and the worker are acting
  // on. Assigned synchronously at script evaluation, before ``init``'s first
  // await, so a consumer that runs early gets a real answer rather than the
  // ``navigator.onLine`` fallback.
  //
  // Frozen, like the other ``window.pwa*`` surfaces (docs/offline-map.md): the
  // mode itself is owned by the service worker, and nothing on the page may
  // assert connectivity by assignment.
  window.pwaConnectivity = Object.freeze({
    /**
     * True when the app is using the network: the interface is up and no
     * offline mode — latched or user-forced — is in force.
     *
     * @returns {boolean}
     */
    isOnline: () => effectiveOnline(),
  });

  init();
})();
