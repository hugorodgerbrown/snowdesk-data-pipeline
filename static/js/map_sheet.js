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
 * sheet with its own open/close (the weather and route sheets) calls
 * ``window.MapSheet.bindDetents`` itself and drives the returned
 * controller from its own open and close.
 *
 * What the binding does, and only on a phone:
 *
 *   - opens the sheet at its ``initial`` detent and measures PEEK from the
 *     element the content marks ``data-sheet-peek``;
 *   - lets the handle and any ``data-sheet-drag`` header drag it, and on
 *     release settles at the detent ``pwaSheetDetentsCore.settle`` picks
 *     from the release height and velocity — or closes it, for a drag below
 *     the lowest detent;
 *   - turns a press on the handle (a click, Enter or Space) into a step up
 *     to the next detent, so the three states are reachable without a drag;
 *   - after every settle, pads the map camera's bottom edge by the sheet's
 *     height and lifts the roundel column onto the sheet's edge, so a
 *     fly-to lands above the sheet rather than behind it. The padding is
 *     applied with the map's view held still (the centre is moved to the
 *     point already under the new padded centre), so a settle never jolts
 *     the ground the reader is looking at;
 *   - puts the handle back as the first child whenever a content swap
 *     removed it, and re-measures PEEK after one.
 *
 * At ``sm`` and up the binding does nothing visible: every height rule is
 * inside a phone media query in static/css/map.css, and the padding and the
 * lift are reset to nothing. The rules themselves are pure and live in
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

  // Only the last stretch of a drag says how fast it was released.
  var VELOCITY_WINDOW_MS = 100;

  /** @returns {boolean} */
  function isPhone() {
    if (typeof window.matchMedia !== 'function') return true;
    return !window.matchMedia(DESKTOP_QUERY).matches;
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

  /** @param {number} px */
  function liftControls(px) {
    document.documentElement.style.setProperty('--map-sheet-lift', px + 'px');
  }

  /**
   * Mark the root while a detent sheet is open on a phone, which is what
   * map.css reads to step every map control but locate out of the way.
   *
   * @param {string|null} detent
   */
  function markSheet(detent) {
    if (detent) document.documentElement.setAttribute('data-map-sheet', detent);
    else document.documentElement.removeAttribute('data-map-sheet');
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
    var detents = core.parseDetents(el.getAttribute('data-detents'));
    if (!detents.length) return null;
    var opts = options || {};
    var handle = /** @type {HTMLElement|null} */ (el.querySelector('[data-sheet-handle]'));
    var initial = core.initialDetent(detents, el.getAttribute('data-detent-initial'));

    /** @type {string|null} */
    var current = null;
    /** @type {{ id: number, startY: number, startHeight: number, moved: boolean, fromHandle: boolean, samples: Array<{t: number, h: number}> }|null} */
    var drag = null;
    var suppressClick = false;
    // The bottom padding this sheet last gave the camera, or 0. A fit that
    // passes its own padding (fitBounds) replaces it, so it is put back
    // when the camera comes to rest.
    var padded = 0;
    var watchingCamera = false;

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

    function peekHeight() {
      var marker = el.querySelector('[data-sheet-peek]');
      if (!marker) return PEEK_FALLBACK_PX;
      var top = el.getBoundingClientRect().top;
      var bottom = marker.getBoundingClientRect().bottom;
      var padding = parseFloat(window.getComputedStyle(el).paddingBottom);
      var gap = Number.isFinite(padding) ? Math.max(PEEK_BOTTOM_GAP_PX, padding) : PEEK_BOTTOM_GAP_PX;
      var measured = Math.round(bottom - top + el.scrollTop + gap);
      return measured > 0 ? Math.min(measured, fullHeight()) : PEEK_FALLBACK_PX;
    }

    /** @returns {Record<string, number>} */
    function heights() {
      return {
        peek: peekHeight(),
        half: Math.round(window.innerHeight * 0.5),
        full: fullHeight(),
      };
    }

    /**
     * Padding and lift for the detent the sheet now rests at. At full the
     * map is covered, so the column drops back behind the sheet and the
     * padding is left at the previous detent's, which means a drag back
     * down does not move the camera twice.
     */
    function applySurroundings() {
      if (!isOpen() || !current || !isPhone()) {
        markSheet(null);
        liftControls(0);
        padded = 0;
        padMapBottom(0);
        return;
      }
      markSheet(current);
      if (current === 'full') {
        liftControls(0);
        return;
      }
      var lift = Math.round(heights()[current] + bottomOffset());
      liftControls(controlsFitAbove(lift) ? lift : 0);
      padded = lift;
      watchCamera();
      padMapBottom(lift);
    }

    /**
     * Put the sheet's padding back after a camera move that replaced it.
     * fitBounds and friends take a padding of their own and leave it on the
     * map, which would let every later centring land under the sheet.
     * padMapBottom is a no-op when the padding already matches, so its own
     * jumpTo ends here once and stops.
     */
    function watchCamera() {
      var map = getMap();
      if (watchingCamera || !map || typeof map.on !== 'function') return;
      watchingCamera = true;
      map.on('moveend', function () {
        if (padded > 0 && isOpen() && isPhone()) padMapBottom(padded);
      });
    }

    /**
     * Whether the roundel column still fits between the map's top edge and
     * a sheet whose top is `lift` px above the viewport's foot. At half on
     * a short phone it does not, and a column lifted anyway runs up under
     * the search control and off the top of the map; it stays behind the
     * sheet instead, as it does at full.
     *
     * @param {number} lift
     * @returns {boolean}
     */
    function controlsFitAbove(lift) {
      var controls = document.getElementById('map-controls-br');
      var mapEl = document.getElementById('map');
      if (!controls || !mapEl) return true;
      var room = window.innerHeight - lift - mapEl.getBoundingClientRect().top;
      return controls.getBoundingClientRect().height + 16 <= room;
    }

    /** @param {string} name */
    function set(name) {
      if (detents.indexOf(name) === -1) return;
      current = name;
      if (name === 'peek') {
        el.style.setProperty('--sheet-peek-height', peekHeight() + 'px');
      }
      el.setAttribute('data-detent', name);
      applySurroundings();
    }

    function open() {
      restoreHandle();
      set(/** @type {string} */ (initial));
    }

    function reset() {
      current = null;
      drag = null;
      padded = 0;
      suppressClick = false;
      el.removeAttribute('data-detent');
      el.removeAttribute('data-sheet-dragging');
      el.style.removeProperty('--sheet-drag-height');
      markSheet(null);
      liftControls(0);
      padMapBottom(0);
    }

    function restoreHandle() {
      if (handle && !el.contains(handle)) el.insertBefore(handle, el.firstChild);
    }

    // Every caller fills the sheet by replacing its children (innerHTML, an
    // HTMX swap), which takes the handle with them. Put it back, and re-fit
    // PEEK to the new content.
    if (typeof MutationObserver === 'function') {
      new MutationObserver(function () {
        restoreHandle();
        if (current === 'peek' && isOpen()) set('peek');
      }).observe(el, { childList: true });
    }

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

    el.addEventListener('pointerdown', function (event) {
      // A new press starts a new gesture: a suppression left by a drag
      // whose finger lifted off the handle (no click followed) ends here.
      suppressClick = false;
      if (!current || !isPhone() || drag) return;
      if (!isDragSurface(event.target)) return;
      var now = event.timeStamp || Date.now();
      var startHeight = el.getBoundingClientRect().height;
      drag = {
        id: event.pointerId,
        startY: event.clientY,
        startHeight: startHeight,
        moved: false,
        fromHandle: !!(handle && handle.contains(/** @type {Node} */ (event.target))),
        samples: [{ t: now, h: startHeight }],
      };
    });

    el.addEventListener('pointermove', function (event) {
      if (!drag || event.pointerId !== drag.id) return;
      var dy = drag.startY - event.clientY;
      if (!drag.moved) {
        if (Math.abs(dy) < DRAG_SLOP_PX) return;
        drag.moved = true;
        var target = /** @type {HTMLElement} */ (event.target);
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
      // Only the handle reads clicks, so only a drag that began on it can
      // leave one to swallow; a header drag setting this would eat the
      // reader's next real press of the handle instead.
      suppressClick = finished.fromHandle;
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

    if (handle) {
      handle.addEventListener('click', function () {
        if (suppressClick) {
          suppressClick = false;
          return;
        }
        var next = core.nextDetent(detents, current);
        if (next) set(next);
      });
    }

    window.addEventListener('resize', function () {
      if (!current) return;
      if (current === 'peek') {
        el.style.setProperty('--sheet-peek-height', peekHeight() + 'px');
      }
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
   * @param {{ triggerSelector?: string, onBeforeOpen?: function(): void }} [options]
   *   ``triggerSelector`` — the control that opens the sheet. Its own click
   *   handler does the opening, so a click on it must not also be read as
   *   an outside click and close what just opened.
   *   ``onBeforeOpen`` — runs before the sheet is revealed.
   * @returns {{ open: function(): void, close: function(): void, isOpen: function(): boolean, element: HTMLElement }}
   */
  function attach(el, options) {
    var opts = options || {};

    function isOpen() {
      return !el.hasAttribute('hidden');
    }

    // SNOW-1097: null unless the partial declared detents.
    var detents = bindDetents(el, { onClose: close });

    /**
     * Everything a close does beyond hiding. Split out because a dismissal
     * routed through overlays.js has already hidden the element.
     */
    function teardown() {
      window.PlacePicker?.deactivate();
      el.innerHTML = '';
      if (detents) detents.reset();
    }

    function open() {
      // SNOW-658: only one overlay is open over the map at a time. Announce
      // BEFORE the sheet is revealed, so the surface being replaced is gone
      // by the time this one is on screen.
      window.pwaMapOverlays?.opening(el.id);
      if (opts.onBeforeOpen) opts.onBeforeOpen();
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
      close();
    });

    // SNOW-658: one mechanism for "only one overlay at a time", registered
    // here rather than by each caller — both sheets reach the map through
    // this controller, so this is the one place that knows they are map
    // overlays at all.
    window.pwaMapOverlays?.register(el.id, { isOpen: isOpen, close: close });

    return { open: open, close: close, isOpen: isOpen, element: el };
  }

  window.MapSheet = {
    attach: attach,
    bindDetents: bindDetents,
    toast: toast,
    canQueueMutations: canQueueMutations,
  };
}());
