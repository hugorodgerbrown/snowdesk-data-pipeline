/*
 * static/js/map_sheet.js — Shared sheet controller, toast and mutation-
 * storage guard for the map's bottom sheets (SNOW-608).
 *
 * ``favourites.js`` and ``report.js`` drive two sheets that sit on the same
 * page and behave identically: the same open/focus cycle, the same four
 * ways to close (Escape, click-outside, a [data-action="dismiss"] control,
 * a programmatic close), the same teardown, the same failure toast and the
 * same "can this device queue a mutation?" check. They implemented all of
 * it twice, which is how the two surfaces ended up wording the same
 * failure differently — one saying a favourite could not be removed, the
 * other that something went wrong. Finding D4 in
 * ``docs/code-reviews/2026-08-03-js-review.md`` has the correspondence.
 *
 * Three exports on ``window.MapSheet``:
 *
 *   attach(el, options)   → a controller for one sheet element.
 *   toast(message)        → reveal the shared failure toast.
 *   canQueueMutations()   → false when this device cannot persist a
 *                           queued mutation.
 *
 * Loaded once from ``public/home.html`` ahead of both surface partials
 * (all three are ``defer``, so document order is execution order).
 *
 * Sheet lifecycle
 * ---------------
 * ``attach`` wires the three dismissal routes the two modules shared, so a
 * caller only supplies what genuinely differs: the trigger it must not
 * treat as an outside click, and any work to do before the sheet is
 * revealed.
 *
 * SNOW-658: it also registers the sheet with
 * ``window.pwaMapOverlays`` (static/js/map_overlay_exclusivity.js) under
 * the sheet's own DOM id, and announces every open through it — so
 * opening either sheet closes the layers menu, the downloads sheet and
 * any anchored detail popup, and each of those closes this one. Both
 * sheets get that from ``attach``; neither names a sibling.
 *
 * The teardown — deactivate the place-picker, empty the sheet — runs on
 * every route, including a dismissal that ``overlays.js`` handled. That
 * shared handler (SNOW-486) hides a ``[data-overlay]`` element on any
 * ``[data-action="dismiss"]`` click and dispatches ``overlay:dismissed``;
 * it knows nothing about the place-picker, so the controller listens for
 * that event and finishes the job. Hiding is deliberately not repeated
 * there — the element is already hidden by the time the event fires.
 *
 * Detents (SNOW-1097)
 * --------------------
 * Below the ``sm`` breakpoint a sheet whose partial declares ``detents``
 * (includes/_overlay_sheet.html) rests at peek, half or full rather than
 * growing with its content. ``bindDetents`` is that behaviour, and
 * ``attach`` calls it for every sheet that declares detents, so a sheet
 * driven by ``attach`` needs nothing more than the template parameter. A
 * sheet with its own open/close (the route sheet) calls
 * ``window.MapSheet.bindDetents`` itself and drives the returned
 * controller from its own open and close.
 *
 * What the binding does, and only on a phone:
 *
 *   - opens the sheet at its ``initial`` detent and measures PEEK from the
 *     element the content marks ``data-sheet-peek``; HALF and FULL fit the
 *     content too (SNOW-1099), HALF down to a ``data-sheet-half`` element
 *     or the content's end, capped at half the screen, FULL to the
 *     content's end, capped below the top inset;
 *   - lets the handle and any ``data-sheet-drag`` header drag it, and on
 *     release settles at the detent ``pwaSheetDetentsCore.settle`` picks
 *     from the release height and velocity — or closes it, for a drag below
 *     the lowest detent;
 *   - lets a finger anywhere on the body drag it too (Hugo, 25 Oct 2026),
 *     as Google and Apple Maps do: below the tallest detent an up or down
 *     drag moves the sheet, while a sideways one is left to the content;
 *     at the tallest detent the body scrolls, and a pull down at the top
 *     of the scroll takes the sheet down with it. Fields, menus and
 *     anything marked ``data-sheet-no-drag`` keep their own press, and a
 *     mouse keeps the handle and the header only;
 *   - turns a press on the handle (a click, Enter or Space), or a tap on a
 *     ``data-sheet-drag`` header away from its controls, into a step up to
 *     the next detent, so the three states are reachable without a drag;
 *   - after every settle, pads the map camera's bottom edge by the sheet's
 *     height, so a fly-to lands above the sheet rather than behind it. The
 *     map's furniture does not move or fold: the sheet slides up over it.
 *     The padding is
 *     applied with the map's view held still (the centre is moved to the
 *     point already under the new padded centre), so a settle never jolts
 *     the ground the reader is looking at;
 *   - puts the handle back as the first child whenever a content swap
 *     removed it, and re-fits the resting detent after one, or after any
 *     child changes height in place (a ResizeObserver; never mid-drag).
 *
 * Two rules hold ACROSS sheets, because every detent sheet docks to the same
 * foot of the screen (2026-10-19): opening one closes any other that is open
 * (the route sheet included, which window.pwaMapOverlays does not reach),
 * and the camera padding and the root's `data-map-sheet` have one writer
 * here — each sheet reports its lift and detent, and the tallest lift wins,
 * re-applied by one moveend watcher. Per-sheet watchers used to fight each
 * other's padding until the stack overflowed and the map stopped answering.
 *
 * At ``sm`` and up the binding does nothing visible: every height rule is
 * inside a phone media query in static/css/map.css, and the padding and the
 * root mark are reset to nothing. The rules themselves are pure and live in
 * static/js/sheet_detents_core.js. If that module is not on the page (most
 * Vitest suites import this file alone) ``bindDetents`` returns null and the
 * sheet behaves exactly as it did before detents existed.
 *
 * Toast
 * -----
 * One server-rendered ``includes/_toast.html`` instance
 * (``#map-sheet-toast``, included once by home.html), revealed with the
 * caller's text rather than built from ``createElement`` in two places.
 * That makes the markup, the tokens and the ARIA roles the partial's
 * business rather than this module's, and leaves the "×" to
 * ``overlays.js``'s delegated handler like every other toast on the site.
 *
 * The auto-dismiss timer is owned here rather than delegated to the
 * partial's ``timeout`` option, because that timer is armed by a
 * MutationObserver watching for the toast becoming visible — a second
 * message while the toast is already up changes only its text, so no
 * observer fires and the first message's timer would still be the one
 * running.
 */

(function () {
  'use strict';

  // Matches the 6s both surfaces used before this module existed.
  var TOAST_TIMEOUT_MS = 6000;

  var TOAST_ID = 'map-sheet-toast';

  /** @type {number|undefined} */
  var toastTimer;

  /**
   * Reveal the shared toast with ``message``.
   *
   * A no-op when the partial is absent — the module loads on any page that
   * includes it, and a missing toast must not break the caller's flow (the
   * callers are all failure paths already).
   *
   * @param {string} message
   */
  function toast(message) {
    var el = document.getElementById(TOAST_ID);
    if (!el) return;

    var body = el.querySelector('[data-toast-body]');
    if (body) body.textContent = message;

    if (toastTimer) clearTimeout(toastTimer);
    el.classList.remove('hidden');
    toastTimer = setTimeout(function () {
      el.classList.add('hidden');
      toastTimer = undefined;
    }, TOAST_TIMEOUT_MS);
  }

  // A manual "×" is handled by overlays.js, which hides the toast without
  // knowing about the timer above. Drop it so a later reveal is not cut
  // short by a timer armed for the message the user already dismissed.
  document.addEventListener('overlay:dismissed', function (event) {
    var el = event.detail && event.detail.overlay;
    if (!el || el.id !== TOAST_ID) return;
    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = undefined;
    }
  });

  /**
   * Whether a mutation enqueued now would actually be persisted.
   *
   * ``pwaMutationQueue.enqueue()`` is defensively non-fatal: with IndexedDB
   * missing, or in the terminal Reset-Required state, it drops the
   * operation and still resolves (the SNOW-375/376 contract). Awaiting it
   * therefore cannot tell a caller whether anything was stored, so every
   * caller checks up front instead and shows a toast rather than an
   * optimistic confirmation for work that was never queued.
   *
   * @returns {boolean}
   */
  function canQueueMutations() {
    if (!window.pwaMutationQueue) return false;
    if (typeof window.pwaDb !== 'object') return false;
    if (
      typeof window.pwaDb.isResetRequired === 'function' &&
      window.pwaDb.isResetRequired()
    ) {
      return false;
    }
    return true;
  }

  // Tailwind's `sm` breakpoint — the width at which a sheet stops being a
  // bottom dock and becomes the inset desktop panel. Detents exist only
  // below it; static/css/map.css's phone query is the same line.
  var DESKTOP_QUERY = '(min-width: 640px)';

  // PEEK for content that marks no `data-sheet-peek` element: enough for a
  // title and one line under the handle. The CSS carries the same fallback.
  var PEEK_FALLBACK_PX = 160;

  // Space kept below the peek element's bottom edge, matching the sheet's
  // own top padding so the summary sits centred in what shows. The sheet's
  // computed bottom padding wins when larger: on a phone with a home
  // indicator it carries the safe-area inset, and a peek that ignored it
  // would put its one row under the indicator.
  var PEEK_BOTTOM_GAP_PX = 16;

  // A pointer that travels less than this is a press, not a drag — the
  // handle's click then steps to the next detent instead.
  var DRAG_SLOP_PX = 4;

  // A finger on the sheet's BODY must travel further before it is read as
  // a drag, and the axis it travelled most along decides what it is: up or
  // down moves the sheet, sideways is left to the content (a scroller, the
  // route profile's scrub). Four pixels is too little to tell the two apart.
  var BODY_SLOP_PX = 8;

  // A click this soon after a body drag ends is the drag's own, landing on
  // whatever link or button the finger lifted over; it is swallowed.
  var BODY_CLICK_GRACE_MS = 400;

  // Content that keeps a press to itself on a phone sheet: fields, menus,
  // and anything that marks itself `data-sheet-no-drag` (the weather
  // timeline, which scrubs the hour on any movement).
  var BODY_NO_DRAG = 'input, select, textarea, [contenteditable="true"], [role="menu"], [role="slider"], [data-sheet-no-drag]';

  // Only the last stretch of a drag says how fast it was released.
  var VELOCITY_WINDOW_MS = 100;

  // How long a desktop panel takes to grow or shrink to new content — the
  // CSS transition in static/css/map.css's `[data-sheet-morphing]` rule. The
  // inline height is let go a little after it, so the panel is back to
  // fitting its content even when no transitionend arrives.
  var MORPH_MS = 240;
  var MORPH_RELEASE_MS = MORPH_MS + 60;

  // The longest a sheet handed over from another waits at the other's
  // height for its body before settling on what it has (a loading line).
  var HOLD_MS = 1500;

  /** @returns {boolean} */
  function isPhone() {
    if (typeof window.matchMedia !== 'function') return true;
    return !window.matchMedia(DESKTOP_QUERY).matches;
  }

  /** @returns {boolean} Whether the reader asked for less motion. */
  function reducedMotion() {
    return typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /** @returns {any} The MapLibre map, when the map bundle has made one. */
  function getMap() {
    return typeof MAP !== 'undefined' ? MAP : null;
  }

  /**
   * Pad the map camera's bottom edge by `bottom` px without moving what the
   * reader sees: the centre moves to the point already under the new padded
   * centre, so only the camera's notion of "the middle" changes.
   *
   * @param {number} bottom
   */
  function padMapBottom(bottom) {
    var map = getMap();
    if (!map || typeof map.getPadding !== 'function') return;
    var padding = map.getPadding() || {};
    if ((padding.bottom || 0) === bottom) return;
    // A jumpTo now would cancel a flight in progress — a search pick frames
    // its region just as the sheet's content lands and re-fits PEEK. The
    // sheet's moveend watcher puts the padding on once the camera rests.
    if (typeof map.isMoving === 'function' && map.isMoving()) return;
    var next = {
      top: padding.top || 0,
      left: padding.left || 0,
      right: padding.right || 0,
      bottom: bottom,
    };
    var canvas = typeof map.getContainer === 'function' ? map.getContainer() : null;
    if (!canvas || typeof map.unproject !== 'function') {
      map.setPadding(next);
      return;
    }
    var width = canvas.clientWidth;
    var height = canvas.clientHeight;
    var centre = map.unproject([
      next.left + (width - next.left - next.right) / 2,
      next.top + (height - next.top - next.bottom) / 2,
    ]);
    map.jumpTo({ center: centre, padding: next });
  }

  // ONE OWNER FOR WHAT SHEETS DO TO THE PAGE (2026-10-19). Every bound sheet
  // used to write the camera's bottom padding and the root's
  // `data-map-sheet` itself, and each kept a moveend watcher that put ITS
  // padding back when the camera came to rest — a closed sheet's being 0.
  // With two sheets bound (any session that had opened a route and then a
  // region), the first camera move set off a loop: one watcher's jumpTo
  // ended a move, the other's answered it, and so on until the browser's
  // stack overflowed inside MapLibre's handler manager, which then stopped
  // answering touches for good. So a sheet now only REPORTS what it needs
  // (its lift, its detent) and this module writes one answer for all of
  // them: the tallest lift, from one watcher, which a jumpTo of its own
  // cannot re-trigger because the second pass finds the padding already
  // matching.

  /** @type {Map<HTMLElement, number>} The camera lift each open sheet wants. */
  var lifts = new Map();
  /** @type {Map<HTMLElement, string>} The detent each open sheet rests at. */
  var restingDetents = new Map();
  /** @type {Array<{ el: HTMLElement, close: (function(): void)|undefined }>} */
  var boundSheets = [];
  /** The map the shared moveend watcher is on, so a new map gets one too. */
  var watchedMap = null;

  /**
   * Forget entries for sheets no longer in the document (a test fixture
   * replaced, a surface re-rendered), so they cannot hold the padding up.
   *
   * @param {Map<HTMLElement, any>} registry
   */
  function prune(registry) {
    registry.forEach(function (_value, el) {
      if (!el.isConnected) registry.delete(el);
    });
  }

  /** Pad the camera's foot by the tallest lift any open sheet wants. */
  function applyMapPadding() {
    prune(lifts);
    var bottom = 0;
    lifts.forEach(function (lift) {
      if (lift > bottom) bottom = lift;
    });
    padMapBottom(bottom);
  }

  /**
   * Record the camera lift one sheet wants (0 for none) and re-apply.
   *
   * @param {HTMLElement} el
   * @param {number} lift
   */
  function setLift(el, lift) {
    if (lift > 0) lifts.set(el, lift);
    else lifts.delete(el);
    watchCamera();
    applyMapPadding();
  }

  /**
   * Put the sheets' padding back after a camera move that replaced it.
   * fitBounds and friends take a padding of their own and leave it on the
   * map, which would let every later centring land under the sheet; a
   * deferred write (the camera was moving) lands here too. One watcher per
   * map, whatever the number of sheets.
   */
  function watchCamera() {
    var map = getMap();
    if (!map || map === watchedMap || typeof map.on !== 'function') return;
    watchedMap = map;
    map.on('moveend', function () {
      if (!isPhone()) return;
      applyMapPadding();
    });
  }

  /**
   * Mark the root with the detent while a detent sheet is open on a phone,
   * for any rule that needs to know a sheet is up. Nothing on the map
   * moves for it — the sheet slides up over the furniture (Hugo, 16 Oct).
   * A sheet closing clears only its own entry, so it cannot unmark the root
   * under another sheet still open.
   *
   * @param {HTMLElement} el
   * @param {string|null} detent
   */
  function markSheet(el, detent) {
    if (detent) {
      restingDetents.delete(el);
      restingDetents.set(el, detent);
    } else {
      restingDetents.delete(el);
    }
    prune(restingDetents);
    var latest = null;
    restingDetents.forEach(function (value) {
      latest = value;
    });
    if (latest) document.documentElement.setAttribute('data-map-sheet', latest);
    else document.documentElement.removeAttribute('data-map-sheet');
  }

  /**
   * The detent a sheet opening next should rest at, when the reader got
   * there from another panel (Hugo, 25 Oct 2026): a phone sheet read at
   * FULL that hands over to the next place's panel — a row in it, or the
   * back pill — opens that panel at FULL too, not at its peek. Recorded by
   * ``handOff`` while the old sheet is still up (its close forgets its
   * detent), and taken by the next sheet to open; a hand-off nothing opens
   * for within a few seconds (a resort the map has no pin for, which goes
   * to its page) is dropped rather than left to surprise a later tap.
   *
   * @type {{ detent: string, at: number }|null}
   */
  var handedOff = null;
  var HAND_OFF_MS = 5000;

  /**
   * Remember the detent the open detent sheet rests at, for the panel the
   * reader is about to open from it.
   *
   * @returns {void}
   */
  function handOff() {
    prune(restingDetents);
    var latest = null;
    restingDetents.forEach(function (value) {
      latest = value;
    });
    handedOff = latest ? { detent: latest, at: Date.now() } : null;
  }

  /**
   * The handed-off detent, once: null when there is none or it went stale.
   *
   * @returns {string|null}
   */
  function takeHandOff() {
    var taken = handedOff;
    handedOff = null;
    if (!taken || Date.now() - taken.at > HAND_OFF_MS) return null;
    return taken.detent;
  }

  /**
   * ONE BOTTOM SHEET AT A TIME ON A PHONE (2026-10-19). Every detent sheet
   * docks to the same foot of the screen, so a second one opening lands on
   * top of the first — the region sheet's peek sat over the route sheet's.
   * AND ONE LEFT COLUMN ON A DESKTOP (2026-10-21): the detent sheets are the
   * region, place and weather sheets and the route panel, and from 640px
   * all four dock in the column under the search pill, so a second one
   * there would land on the first just the same. The rule holds at every
   * width.
   * window.pwaMapOverlays cannot settle this: the route sheet stays out of
   * it on purpose (route_rail.js, "its own lifetime"), so nothing it
   * announces would close it and it announces nothing. The rule is stated
   * here instead, where every detent sheet is bound: opening one closes
   * every other through its owner's own close, so each teardown runs.
   *
   * @param {HTMLElement} el The sheet that is opening.
   */
  function closeOtherSheets(el) {
    boundSheets = boundSheets.filter(function (entry) {
      return entry.el.isConnected;
    });
    boundSheets.forEach(function (entry) {
      if (entry.el === el || entry.el.hasAttribute('hidden') || !entry.close) return;
      entry.close();
    });
  }

  /**
   * The height of the detent sheet open now, other than `el`: 0 when none
   * is. Read by a sheet about to open, BEFORE pwaMapOverlays closes the
   * other one, so the new sheet can take over at the height the old one
   * stood at instead of the reader watching one drop and the next rise.
   *
   * @param {HTMLElement} el The sheet that is opening.
   * @returns {number}
   */
  function openSheetHeight(el) {
    var height = 0;
    boundSheets.forEach(function (entry) {
      if (entry.el === el || !entry.el.isConnected || entry.el.hasAttribute('hidden')) return;
      height = Math.max(height, entry.el.getBoundingClientRect().height);
    });
    return height;
  }

  /**
   * Give one sheet the phone detents its partial declares.
   *
   * @param {HTMLElement} el - The sheet container, carrying `data-detents`.
   * @param {{ onClose?: function(): void }} [options]
   *   ``onClose`` — how the sheet closes when a drag pulls it below its
   *   lowest detent. The owner's own close, so its teardown runs as usual.
   * @returns {{ open: function(): void, reset: function(): void, set: function(string): void, current: function(): (string|null) }|null}
   *   Null when the sheet declares no detents or the core is absent.
   */
  function bindDetents(el, options) {
    var core = window.pwaSheetDetentsCore;
    if (!core || !el) return null;
    var declared = core.parseDetents(el.getAttribute('data-detents'));
    if (!declared.length) return null;
    // The detents in force: the declared ones, narrowed by the content when
    // it marks an element `data-sheet-detents` (a bare map pin's sheet drops
    // FULL — there is nothing to reveal).
    var detents = declared;
    var opts = options || {};
    var handle = /** @type {HTMLElement|null} */ (el.querySelector('[data-sheet-handle]'));
    var initial = core.initialDetent(detents, el.getAttribute('data-detent-initial'));

    /** @type {string|null} */
    var current = null;
    /** @type {{ id: number, startX: number, startY: number, startHeight: number, moved: boolean, body: boolean, samples: Array<{t: number, h: number}> }|null} */
    var drag = null;
    var suppressClick = false;
    // When a body drag ended: the click that follows it is swallowed.
    var bodyDragEndedAt = -Infinity;
    boundSheets.push({ el: el, close: opts.onClose });

    function isOpen() {
      return !el.hasAttribute('hidden');
    }

    /** The bottom of the sheet's own box, from the viewport's bottom. */
    function bottomOffset() {
      return Math.max(0, window.innerHeight - el.getBoundingClientRect().bottom);
    }

    function fullHeight() {
      var max = parseFloat(window.getComputedStyle(el).maxHeight);
      return Number.isFinite(max) && max > 0 ? max : window.innerHeight;
    }

    /**
     * The sheet height that ends just below a viewport y: the distance from
     * the sheet's top, plus whatever it has scrolled, plus the gap the
     * bottom padding keeps.
     *
     * @param {number} bottom
     * @returns {number}
     */
    function heightTo(bottom) {
      var top = el.getBoundingClientRect().top;
      var padding = parseFloat(window.getComputedStyle(el).paddingBottom);
      var gap = Number.isFinite(padding) ? Math.max(PEEK_BOTTOM_GAP_PX, padding) : PEEK_BOTTOM_GAP_PX;
      return Math.round(bottom - top + el.scrollTop + gap);
    }

    function peekHeight() {
      var marker = el.querySelector('[data-sheet-peek]');
      if (!marker) return PEEK_FALLBACK_PX;
      var measured = heightTo(marker.getBoundingClientRect().bottom);
      return measured > 0 ? Math.min(measured, fullHeight()) : PEEK_FALLBACK_PX;
    }

    /**
     * The height the whole content needs: down to the lowest child that has
     * a box (a `display: none` child has none; a `visibility: hidden` one
     * keeps its place and counts). Null before anything is laid out.
     *
     * @returns {?number}
     */
    function contentHeight() {
      var bottom = -Infinity;
      Array.prototype.forEach.call(el.children, function (child) {
        var box = child.getBoundingClientRect();
        if (box.height > 0 && box.bottom > bottom) bottom = box.bottom;
      });
      if (!Number.isFinite(bottom)) return null;
      var measured = heightTo(bottom);
      return measured > 0 ? measured : null;
    }

    /**
     * HALF FITS ITS CONTENT (Hugo, 16 Oct), up to half the screen: down to
     * the element the content marks `data-sheet-half` when it marks one
     * (the route sheet stops under its profile), else to the end of the
     * content. A sheet with less to say than half a screen stops where it
     * ends rather than showing a band of empty card.
     *
     * A marker that reads `data-sheet-half="fit"` is capped at FULL's room
     * instead of half the screen: the route sheet's HALF is the point
     * header under a peek that already holds the profile, which on a short
     * phone runs past half the screen, and a non-staff route sheet has no
     * FULL to reveal what a half-screen cap would clip.
     */
    function halfHeight() {
      var marker = firstShownHalfMarker();
      var cap = marker && marker.getAttribute('data-sheet-half') === 'fit'
        ? fullHeight()
        : Math.round(window.innerHeight * 0.5);
      var measured = marker ? heightTo(marker.getBoundingClientRect().bottom) : contentHeight();
      return measured && measured > 0 ? Math.min(cap, measured) : cap;
    }

    /**
     * The first `data-sheet-half` element that has a box. Content may mark
     * one per alternative (the resort forecast marks the today tile in each
     * Base / Mid / Summit pane, and only the chosen pane is displayed), so
     * a hidden one must not stand for the shown one. None shown (the chosen
     * point has no forecast, so its pane has no today tile) is no marker:
     * half then fits the content, rather than a hidden "fit" marker
     * lifting it to FULL's room.
     *
     * @returns {Element|null}
     */
    function firstShownHalfMarker() {
      var markers = el.querySelectorAll('[data-sheet-half]');
      for (var i = 0; i < markers.length; i++) {
        if (markers[i].getBoundingClientRect().height > 0) return markers[i];
      }
      return null;
    }

    /** FULL fits its content too, up to the room below the top inset. */
    function fullFitHeight() {
      var cap = fullHeight();
      var measured = contentHeight();
      return measured ? Math.min(cap, measured) : cap;
    }

    /** @returns {Record<string, number>} */
    function heights() {
      return {
        peek: peekHeight(),
        half: halfHeight(),
        full: fullFitHeight(),
      };
    }

    /**
     * The root mark and the camera padding for the detent the sheet now
     * rests at. The map's furniture does not move: the sheet covers it. At
     * full the map is covered too, so the padding is left at the previous
     * detent's, which means a drag back down does not move the camera twice.
     */
    function applySurroundings() {
      if (!isOpen() || !current || !isPhone()) {
        markSheet(el, null);
        setLift(el, 0);
        return;
      }
      markSheet(el, current);
      if (current === 'full') return;
      setLift(el, Math.round(heights()[current] + bottomOffset()));
    }

    /** @param {string} name */
    function set(name) {
      if (detents.indexOf(name) === -1) return;
      current = name;
      // The detent goes on first: content may show or hide with it, and
      // the height is measured from what the detent shows.
      el.setAttribute('data-detent', name);
      fit(name);
      applySurroundings();
    }

    /**
     * Write the measured height of one detent onto the sheet.
     *
     * @param {string} name
     */
    function fit(name) {
      var measured = heights();
      if (!Object.prototype.hasOwnProperty.call(measured, name)) return;
      var value = measured[name] + 'px';
      var prop = '--sheet-' + name + '-height';
      // Unchanged writes are skipped: the resize observer below fires on
      // this sheet's own children, and a no-op write must not loop it.
      if (el.style.getPropertyValue(prop) !== value) el.style.setProperty(prop, value);
    }

    function open() {
      closeOtherSheets(el);
      restoreHandle();
      syncAllowed();
      // From another panel, the height the reader had it at; the content
      // arriving later can still bring it down (the observer below) when
      // it does not allow that height.
      var carried = takeHandOff();
      // Re-read at every open: a sheet that serves two kinds of content
      // can ask for a different first detent per kind (the place sheet
      // opens a resort at HALF, a saved pin at PEEK; Hugo, 27 Oct 2026).
      initial = core.initialDetent(detents, el.getAttribute('data-detent-initial'));
      set(carried && detents.indexOf(carried) !== -1 ? carried : /** @type {string} */ (initial));
    }

    function reset() {
      current = null;
      drag = null;
      suppressClick = false;
      el.removeAttribute('data-detent');
      el.removeAttribute('data-sheet-dragging');
      el.style.removeProperty('--sheet-drag-height');
      el.style.removeProperty('--sheet-half-height');
      el.style.removeProperty('--sheet-full-height');
      markSheet(el, null);
      setLift(el, 0);
    }

    /** Re-read the content's `data-sheet-detents`, if it marks one. */
    function syncAllowed() {
      var marker = el.querySelector('[data-sheet-detents]');
      if (!marker) {
        detents = declared;
        return;
      }
      var allowed = core.parseDetents(marker.getAttribute('data-sheet-detents'));
      var narrowed = declared.filter(function (name) {
        return allowed.indexOf(name) !== -1;
      });
      detents = narrowed.length ? narrowed : declared;
    }

    function restoreHandle() {
      if (handle && !el.contains(handle)) el.insertBefore(handle, el.firstChild);
    }

    // Every caller fills the sheet by replacing its children (innerHTML, an
    // HTMX swap), which takes the handle with them. Put it back, and re-fit
    // the resting detent to the new content.
    if (typeof MutationObserver === 'function') {
      new MutationObserver(function () {
        restoreHandle();
        syncAllowed();
        if (!current || !isOpen()) return;
        // New content that no longer allows the detent the sheet rests at
        // (FULL, for a day with no bulletin) brings it down to the highest
        // it does allow.
        if (detents.indexOf(current) === -1) set(detents[detents.length - 1]);
        else set(current);
        observeChildren();
      }).observe(el, { childList: true });
    }

    // Content that changes height IN PLACE (the route sheet's point header
    // replacing its title, a readout filling in) re-fits the detent the
    // sheet rests at; a swap is the observer above's. Not while a finger
    // holds the sheet: the drag height wins until it settles.
    var resizes = typeof ResizeObserver === 'function'
      ? new ResizeObserver(function () {
        if (!current || !isOpen() || el.hasAttribute('data-sheet-dragging')) return;
        fit(current);
        applySurroundings();
      })
      : null;
    function observeChildren() {
      if (!resizes) return;
      resizes.disconnect();
      Array.prototype.forEach.call(el.children, function (child) {
        resizes.observe(child);
      });
    }
    observeChildren();

    /** @param {EventTarget|null} target */
    function isDragSurface(target) {
      var node = /** @type {HTMLElement|null} */ (target);
      if (!node || !node.closest) return false;
      if (handle && handle.contains(node)) return true;
      var header = node.closest('[data-sheet-drag]');
      if (!header || !el.contains(header)) return false;
      // A control inside a draggable header keeps its own press.
      return !node.closest('button, a, input, select, textarea, [role="menu"]');
    }

    /** Whether the sheet rests at the tallest detent it allows. */
    function atTop() {
      return current === detents[detents.length - 1];
    }

    /**
     * The scrollers between a press and the sheet, the sheet included:
     * every element on that path whose content overflows a box that
     * scrolls. Only the tallest detent scrolls (static/css/map.css), so
     * below it this is empty in practice.
     *
     * @param {Element} node
     * @returns {Array<Element>}
     */
    function scrollersFrom(node) {
      /** @type {Array<Element>} */
      var found = [];
      /** @type {Element|null} */
      var at = node;
      while (at && el.contains(at)) {
        if (at.scrollHeight > at.clientHeight + 1) {
          var overflow = window.getComputedStyle(at).overflowY;
          if (overflow === 'auto' || overflow === 'scroll') found.push(at);
        }
        if (at === el) break;
        at = at.parentElement;
      }
      return found;
    }

    /**
     * Whether a press on the sheet's body may become a drag of the sheet
     * (Hugo, 25 Oct 2026): any touch on a phone, not a mouse, which has the
     * wheel for the content and the handle for the sheet, and not on a
     * field, a menu or content that keeps its press. At the tallest detent
     * the body scrolls, so a press on content already scrolled down is the
     * start of a scroll, never a drag: only at the top of the scroll does a
     * pull down take the sheet with it.
     *
     * @param {PointerEvent} event
     */
    function isBodyDragStart(event) {
      if (event.pointerType === 'mouse') return false;
      var node = /** @type {HTMLElement|null} */ (event.target);
      if (!node || !node.closest || !el.contains(node)) return false;
      if (node.closest(BODY_NO_DRAG)) return false;
      if (!atTop()) return true;
      return !scrollersFrom(node).some(function (scroller) {
        return scroller.scrollTop > 0;
      });
    }

    el.addEventListener('pointerdown', function (event) {
      // A new press starts a new gesture: a suppression left by a drag
      // whose finger lifted off the handle (no click followed) ends here.
      suppressClick = false;
      if (!current || !isPhone() || drag) return;
      var body = false;
      if (!isDragSurface(event.target)) {
        if (!isBodyDragStart(event)) return;
        body = true;
      }
      var now = event.timeStamp || Date.now();
      var startHeight = el.getBoundingClientRect().height;
      drag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startHeight: startHeight,
        moved: false,
        body: body,
        samples: [{ t: now, h: startHeight }],
      };
    });

    el.addEventListener('pointermove', function (event) {
      if (!drag || event.pointerId !== drag.id) return;
      var dy = drag.startY - event.clientY;
      if (!drag.moved) {
        if (drag.body) {
          var dx = event.clientX - drag.startX;
          if (Math.max(Math.abs(dx), Math.abs(dy)) < BODY_SLOP_PX) return;
          // Sideways is the content's; and at the tallest detent a push up
          // over content with more below it is a scroll, not a drag.
          var sideways = Math.abs(dx) > Math.abs(dy);
          var scrollsOn = dy > 0 && atTop() && scrollersFrom(/** @type {Element} */ (event.target)).some(function (scroller) {
            return scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 1;
          });
          if (sideways || scrollsOn) {
            drag = null;
            return;
          }
        } else if (Math.abs(dy) < DRAG_SLOP_PX) {
          return;
        }
        drag.moved = true;
        // A body drag captures the pointer on the sheet itself, so content
        // with a gesture of its own (the route profile's scrub) stops
        // hearing a finger that is now moving the sheet.
        var target = /** @type {HTMLElement} */ (drag.body ? el : event.target);
        if (target && target.setPointerCapture) {
          try {
            target.setPointerCapture(event.pointerId);
          } catch (_error) {
            // A synthetic or already-released pointer cannot be captured;
            // the drag still works while the finger stays on the sheet.
          }
        }
        el.setAttribute('data-sheet-dragging', '');
      }
      var height = core.clampHeight(drag.startHeight + dy, fullHeight());
      el.style.setProperty('--sheet-drag-height', height + 'px');
      var now = event.timeStamp || Date.now();
      drag.samples.push({ t: now, h: height });
      while (drag.samples.length > 2 && now - drag.samples[0].t > VELOCITY_WINDOW_MS) {
        drag.samples.shift();
      }
      event.preventDefault();
    });

    /** @param {PointerEvent} event */
    function endDrag(event) {
      if (!drag || event.pointerId !== drag.id) return;
      var finished = drag;
      drag = null;
      if (!finished.moved) return;
      // A drag ends in a click on whatever it began on — the handle or a
      // header, both of which step the sheet on a click (below) — so it
      // swallows that one. The next pointerdown clears it either way.
      suppressClick = true;
      if (finished.body) bodyDragEndedAt = performance.now();
      var first = finished.samples[0];
      var last = finished.samples[finished.samples.length - 1];
      var elapsed = last.t - first.t;
      var velocity = elapsed > 0 ? (last.h - first.h) / elapsed : 0;
      el.removeAttribute('data-sheet-dragging');
      el.style.removeProperty('--sheet-drag-height');
      var next = core.settle({
        detents: detents,
        heights: heights(),
        height: last.h,
        velocity: event.type === 'pointercancel' ? 0 : velocity,
      });
      if (next === 'close') {
        if (opts.onClose) opts.onClose();
        return;
      }
      if (next) set(next);
    }

    el.addEventListener('pointerup', endDrag);
    el.addEventListener('pointercancel', endDrag);

    // The browser must not pan under a body drag. Below the tallest detent
    // the sheet's `touch-action: pan-x` (static/css/map.css) already keeps
    // vertical pans away; at the tallest one the body scrolls, so a drag
    // that moves the sheet, and a pull down at the top of the scroll that
    // is about to, cancel their touchmoves here. Pointer events fire before
    // the touchmove of the same movement, so `moved` is already decided.
    // Pulling down is cancelled before the slop is crossed because by then
    // the browser may have started an overscroll it will not give back.
    el.addEventListener(
      'touchmove',
      function (event) {
        if (!drag || !drag.body || !event.cancelable) return;
        if (drag.moved) {
          event.preventDefault();
          return;
        }
        var touch = event.touches && event.touches[0];
        if (!touch) return;
        var down = touch.clientY - drag.startY;
        if (down > 0 && down >= Math.abs(touch.clientX - drag.startX)) event.preventDefault();
      },
      { passive: false },
    );

    // A body drag that lifts over a link or a button does not press it.
    el.addEventListener(
      'click',
      function (event) {
        if (performance.now() - bodyDragEndedAt > BODY_CLICK_GRACE_MS) return;
        bodyDragEndedAt = -Infinity;
        event.preventDefault();
        event.stopPropagation();
      },
      true,
    );

    // A press on the handle, or a tap on a header the content marks
    // draggable (not on a control inside it), steps up a detent and back
    // to the lowest from the top (2026-10-19). The handle is a 36px bar;
    // the header is the row a reader actually aims at, and it already
    // took a drag, so a tap that did nothing read as a sheet that does not
    // open. The handle is also the keyboard's way in: Enter and Space on
    // it are clicks.
    el.addEventListener('click', function (event) {
      var target = /** @type {Node|null} */ (event.target);
      var onHandle = !!(handle && target && handle.contains(target));
      if (!onHandle && (!isPhone() || !isDragSurface(target))) return;
      if (suppressClick) {
        suppressClick = false;
        return;
      }
      if (!current) return;
      var next = core.nextDetent(detents, current);
      if (next) set(next);
    });

    window.addEventListener('resize', function () {
      if (!current) return;
      fit(current);
      applySurroundings();
    });

    return {
      open: open,
      reset: reset,
      set: set,
      current: function () {
        return current;
      },
    };
  }

  /**
   * Wire one sheet element and return its controller.
   *
   * @param {HTMLElement} el - The sheet container ([data-overlay], hidden).
   * @param {{ triggerSelector?: string, onBeforeOpen?: function(): void, onTeardown?: function(): void }} [options]
   *   ``triggerSelector`` — the control that opens the sheet. Its own click
   *   handler does the opening, so a click on it must not also be read as
   *   an outside click and close what just opened.
   *   ``onBeforeOpen`` — runs before the sheet is revealed.
   *   ``onTeardown`` — runs after every close, by whichever route (Escape,
   *   click-outside, the ×, a drag below peek, another overlay opening).
   *   SNOW-1239: the object sheets abort their request in flight here, so
   *   an answer for a sheet already closed never lands in it.
   * @returns {{ open: function({ awaiting?: boolean }=): void, close: function(): void, isOpen: function(): boolean, detent: function(string): void, pending: function({ keep?: Element|null }=): void, element: HTMLElement }}
   */
  function attach(el, options) {
    var opts = options || {};

    function isOpen() {
      return !el.hasAttribute('hidden');
    }

    // SNOW-1097: null unless the partial declared detents.
    var detents = bindDetents(el, { onClose: close });

    // ONE PANEL, NEW CONTENT (Hugo, 27 Oct 2026). Tapping from one resort,
    // region or weather point to the next used to empty the open sheet down
    // to a loading line and grow it again when the answer landed, so the
    // panel bobbed down and up between every two places. Now the owner
    // marks the open sheet `pending` and keeps what it shows, dimmed, until
    // the new body replaces it in one swap; a phone sheet then glides from
    // one detent height to the next (its height is a CSS transition), and a
    // desktop panel, which is as tall as its content and so has no height
    // to transition, is carried from its old height to its new one here
    // (`morph`). Only the object sheets do this — the ones with detents.
    var lastHeight = 0;
    var morphTimer = 0;
    var holding = false;
    var holdTimer = 0;

    /** Let go of a morph's inline height: the panel fits its content again. */
    function endMorph() {
      clearTimeout(morphTimer);
      el.removeAttribute('data-sheet-morphing');
      el.style.removeProperty('height');
    }

    /**
     * Carry a desktop panel from `from` px to the height its content now
     * needs. Nothing on a phone, where the detent heights transition in CSS,
     * or for a reader who asked for less motion.
     *
     * @param {number} from
     */
    function morph(from) {
      if (!(from > 0) || isPhone() || reducedMotion()) return;
      endMorph();
      var to = el.getBoundingClientRect().height;
      if (Math.abs(to - from) < 2) return;
      el.style.height = from + 'px';
      // The start height is laid out before the transition is switched on
      // and the end height set, or the browser sees one change, not two.
      void el.offsetHeight;
      el.setAttribute('data-sheet-morphing', '');
      el.style.height = to + 'px';
      morphTimer = window.setTimeout(endMorph, MORPH_RELEASE_MS);
    }

    /**
     * Dim what the open sheet shows while the next item's body is fetched,
     * keeping `keep` (and the handle) at full strength — the region sheet
     * repaints its peek row for the new region at once and keeps that.
     *
     * @param {Element} parent
     * @param {Element|null} keep
     */
    function markStale(parent, keep) {
      Array.prototype.forEach.call(parent.children, function (child) {
        if (child.hasAttribute('data-sheet-handle') || child === keep) return;
        if (keep && child.contains(keep)) {
          markStale(child, keep);
          return;
        }
        child.setAttribute('data-sheet-stale', '');
      });
    }

    /**
     * The open sheet is about to show something else: keep the current
     * content up, dimmed, until the swap. (map_panel_trail.js scrolls it
     * back to the top when the swap brings another place.)
     *
     * @param {{ keep?: Element|null }} [options]
     */
    function pending(options) {
      if (!isOpen()) return;
      var o = options || {};
      el.setAttribute('data-sheet-pending', '');
      el.setAttribute('aria-busy', 'true');
      markStale(el, o.keep || null);
    }

    function clearPending() {
      el.removeAttribute('data-sheet-pending');
      el.removeAttribute('aria-busy');
      Array.prototype.forEach.call(el.querySelectorAll('[data-sheet-stale]'), function (node) {
        node.removeAttribute('data-sheet-stale');
      });
    }

    // What is dimmed belongs to the place the reader has left: its star,
    // Share, Rename and Delete still name that place, so they act on
    // nothing until the swap (a press on one is swallowed here, before
    // favourites.js's document handler or the owner's own sees it). The ×
    // still closes the sheet. map.css takes the pointer off them too, so a
    // finger on them drags the sheet as it would anywhere else.
    /** @param {Event} event */
    function swallowStale(event) {
      if (!el.hasAttribute('data-sheet-pending')) return;
      var target = /** @type {Element|null} */ (event.target instanceof Element ? event.target : null);
      if (!target || !target.closest('[data-sheet-stale]')) return;
      if (target.closest('[data-action="dismiss"]')) return;
      event.preventDefault();
      event.stopPropagation();
    }
    if (detents) {
      el.addEventListener('click', swallowStale, true);
      el.addEventListener('submit', swallowStale, true);
    }

    if (detents && typeof ResizeObserver === 'function') {
      new ResizeObserver(function () {
        lastHeight = isOpen() ? el.getBoundingClientRect().height : 0;
      }).observe(el);
    }

    // Every owner fills the sheet by replacing its children, so a swap is a
    // childList change. The height the panel had is the ResizeObserver's
    // last reading (layout already holds the new content by now), or, mid
    // morph, the height the transition has reached.
    var swaps = detents && typeof MutationObserver === 'function'
      ? new MutationObserver(function () {
        if (!isOpen()) return;
        if (holding) {
          releaseHold();
          return;
        }
        clearPending();
        morph(el.hasAttribute('data-sheet-morphing') ? el.getBoundingClientRect().height : lastHeight);
      })
      : null;
    if (swaps) swaps.observe(el, { childList: true });

    /**
     * Everything a close does beyond hiding. Split out because a dismissal
     * routed through overlays.js has already hidden the element.
     */
    function teardown() {
      window.PlacePicker?.deactivate();
      holding = false;
      clearTimeout(holdTimer);
      endMorph();
      el.innerHTML = '';
      clearPending();
      el.removeAttribute('data-sheet-handover');
      lastHeight = 0;
      if (detents) detents.reset();
      if (opts.onTeardown) opts.onTeardown();
    }

    /**
     * Stop holding a handed-over sheet at the old sheet's height: the phone
     * detent's own height takes over through its transition, and a desktop
     * panel is carried to its content's.
     */
    function releaseHold() {
      if (!holding) return;
      holding = false;
      clearTimeout(holdTimer);
      if (isPhone()) {
        el.style.removeProperty('height');
        return;
      }
      morph(el.getBoundingClientRect().height);
    }

    /**
     * @param {{ awaiting?: boolean }} [options]
     *   ``awaiting`` — the owner has asked for the body and shows a loading
     *   line meanwhile. A sheet handed over from another one then holds the
     *   other's height until the body lands (or HOLD_MS passes), so the
     *   loading line does not shrink it to a sliver first.
     */
    function open(options) {
      // HANDED OVER, NOT DROPPED AND RAISED (Hugo, 27 Oct 2026). From a
      // resort to a weather point, or a region to a resort, one object sheet
      // closes as another opens. Read the height the open one stands at
      // before the announcement below closes it, and open this one from
      // there: it resizes to its own height rather than the reader watching
      // one panel slide away and the next slide up from the foot.
      var from = detents && !isOpen() ? openSheetHeight(el) : 0;
      if (reducedMotion()) from = 0;
      var handover = from > 0 && isPhone();
      var hold = from > 0 && !!(options && options.awaiting);
      // SNOW-658: only one overlay is open over the map at a time. Announce
      // BEFORE the sheet is revealed, so the surface being replaced is gone
      // by the time this one is on screen.
      window.pwaMapOverlays?.opening(el.id);
      if (opts.onBeforeOpen) opts.onBeforeOpen();
      if (handover) {
        // No slide from the foot (map.css's @starting-style skips a sheet
        // handed over), and the old sheet's height to start from.
        el.setAttribute('data-sheet-handover', '');
        el.style.height = from + 'px';
      }
      el.removeAttribute('hidden');
      // SNOW-658: on desktop the sheet is a floating card, and
      // includes/_overlay_sheet.html can only put it in a fixed viewport
      // corner — which is where the season scrubber and the bottom-right
      // roundel column already are. window.pwaOverlayBounds measures the room
      // those leave (the same measurement the layers menu makes) and insets
      // the sheet into it. Opt-in per sheet rather than a sweep over
      // [data-overlay]: the same partial renders inline in the component
      // library. A no-op below the `sm` breakpoint, where the bottom dock is
      // the right shape.
      window.pwaOverlayBounds?.positionSheet(el);
      if (detents) detents.open();
      // What the owner painted before opening (its loading line) and the
      // handle put back on the way in are not the body landing.
      if (swaps) swaps.takeRecords();
      if (hold) {
        // The body is on its way: stay at the old height until it lands.
        if (!handover) el.style.height = from + 'px';
        holding = true;
        holdTimer = window.setTimeout(releaseHold, HOLD_MS);
      } else if (handover) {
        // Laid out at the old height, then let go: the detent's own height
        // takes over through the sheet's height transition.
        void el.offsetHeight;
        el.style.removeProperty('height');
      } else if (from > 0) {
        morph(from);
      }
      el.focus();
    }

    function close() {
      el.setAttribute('hidden', '');
      teardown();
    }


    document.addEventListener('overlay:dismissed', function (event) {
      if ((event.detail && event.detail.overlay) !== el) return;
      teardown();
    });

    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      if (!isOpen()) return;
      close();
    });

    // Click-outside closes — except while the user is working the map or
    // the place-picker to position a pin, and except on the trigger, whose
    // own handler owns the open.
    document.addEventListener('click', function (event) {
      if (!isOpen()) return;
      var target = /** @type {HTMLElement} */ (event.target);
      if (!target || !target.closest) return;
      if (el.contains(target)) return;
      if (opts.triggerSelector && target.closest(opts.triggerSelector)) return;
      if (target.closest('#map')) return;
      if (target.closest('[data-place-picker]')) return;
      // A row that opens another map panel, or the back pill, hands over
      // to that panel (map_panel_trail.js). The region sheet's own resort
      // handler opens the resort sheet DURING this click, so without this
      // the newly open sheet read the tap as one outside it and closed
      // straight away (Hugo, 25 Oct 2026).
      if (target.closest('[data-panel-open], [data-panel-back]')) return;
      close();
    });

    // SNOW-658: one mechanism for "only one overlay at a time", registered
    // here rather than by each caller — both sheets reach the map through
    // this controller, so this is the one place that knows they are map
    // overlays at all.
    //
    // SNOW-1195: a sheet that declares detents is an object sheet — the
    // region, place and weather sheets. The calendar spares those, so a
    // change of day reloads the sheet rather than closing it.
    window.pwaMapOverlays?.register(el.id, {
      isOpen: isOpen,
      close: close,
      objectSheet: el.hasAttribute('data-detents'),
    });

    /**
     * Rest a phone sheet at a named detent, as a drag there would (the
     * resort walkthrough raises the place sheet to show the row it asks
     * for, SNOW-1231). Nothing on a desktop, or for a sheet without it.
     *
     * @param {string} name
     * @returns {void}
     */
    function detent(name) {
      if (detents && isOpen() && isPhone()) detents.set(name);
    }

    return {
      open: open,
      close: close,
      isOpen: isOpen,
      detent: detent,
      pending: pending,
      element: el,
    };
  }

  window.MapSheet = {
    attach: attach,
    bindDetents: bindDetents,
    handOff: handOff,
    toast: toast,
    canQueueMutations: canQueueMutations,
  };
}());
