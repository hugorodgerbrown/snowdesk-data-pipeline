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
 * The teardown — deactivate the place-picker, empty the sheet — runs on
 * every route, including a dismissal that ``overlays.js`` handled. That
 * shared handler (SNOW-486) hides a ``[data-overlay]`` element on any
 * ``[data-action="dismiss"]`` click and dispatches ``overlay:dismissed``;
 * it knows nothing about the place-picker, so the controller listens for
 * that event and finishes the job. Hiding is deliberately not repeated
 * there — the element is already hidden by the time the event fires.
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

    /**
     * Everything a close does beyond hiding. Split out because a dismissal
     * routed through overlays.js has already hidden the element.
     */
    function teardown() {
      window.PlacePicker?.deactivate();
      el.innerHTML = '';
    }

    function open() {
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

    return { open: open, close: close, isOpen: isOpen, element: el };
  }

  window.MapSheet = {
    attach: attach,
    toast: toast,
    canQueueMutations: canQueueMutations,
  };
}());
