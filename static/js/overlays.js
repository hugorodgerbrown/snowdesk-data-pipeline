/*
 * static/js/overlays.js — Shared overlay dismiss + toast auto-dismiss (SNOW-486).
 *
 * Loaded on every public page (public/templates/public/base.html, defer).
 * Two independent, self-contained pieces:
 *
 * 1. The "×" standard — one delegated `document` click listener for every
 *    [data-action="dismiss"] control. On click:
 *      - finds the closest [data-overlay] ancestor. A click with no such
 *        ancestor is a silent no-op — some overlays intentionally never
 *        carry [data-overlay] because they own a bespoke show/hide idiom
 *        this generic handler can't safely drive (the mutation-queue
 *        toast's slide transition — templates/includes/_toast_banner.html
 *        — and #offmap-banner's hidden/flex dual toggle — see
 *        public/templates/public/home.html); their own JS keeps binding
 *        its own dismiss listener directly.
 *      - hides it using the idiom the element declares:
 *        `data-overlay-hide="class"` toggles the Tailwind `hidden` CLASS
 *        (every shell-chrome banner/toast/modal); the default toggles the
 *        HTML5 `hidden` ATTRIBUTE (map-internal overlays and sheets —
 *        #home-intro, #map-help-overlay, #favourite-sheet, #report-sheet).
 *        Never flips which idiom an element uses.
 *      - optionally persists a localStorage flag via
 *        `data-overlay-persist="key=value"`.
 *      - dispatches a bubbling `overlay:dismissed` CustomEvent
 *        (`detail.overlay` = the element) so the owning module can run
 *        teardown beyond hide (map_sheet.js deactivates the place-picker
 *        and clears the sheet's content for both map sheets, and drops the
 *        shared toast's auto-dismiss timer; home_intro.js / map_help.js
 *        set aria-hidden and restore focus) without binding its own
 *        ×-click listener.
 *
 * 2. A shared toast auto-dismiss controller — any element carrying
 *    `data-toast-timeout="<ms>"` (an opt-in on templates/includes/_toast.html)
 *    is watched for becoming visible and hidden again that many ms later,
 *    via the same idiom-aware hide as above. The three offline toasts in
 *    public/partials/_map_embed.html opt in at 6000ms; a toast whose text
 *    is rewritten between reveals (map_sheet.js's #map-sheet-toast) runs
 *    its own timer instead, since a text change fires no mutation the
 *    observer below watches for and so would not re-arm this one.
 */

(function overlayDismissInit() {
  'use strict';

  /**
   * Hide `el` using the idiom it declares.
   * @param {HTMLElement} el
   */
  function hideOverlay(el) {
    if (el.dataset.overlayHide === 'class') {
      el.classList.add('hidden');
    } else {
      el.setAttribute('hidden', '');
    }
  }

  /**
   * Whether `el` is currently hidden, per the idiom it declares.
   * @param {HTMLElement} el
   * @returns {boolean}
   */
  function isOverlayHidden(el) {
    return el.dataset.overlayHide === 'class'
      ? el.classList.contains('hidden')
      : el.hasAttribute('hidden');
  }

  /**
   * Persist a `data-overlay-persist="key=value"` declaration to
   * localStorage, tolerating storage errors (private mode / full quota) —
   * matching every other localStorage write in this codebase
   * (home_intro.js, map_help.js, pwa_install.js).
   * @param {string} declaration
   */
  function persist(declaration) {
    const eq = declaration.indexOf('=');
    if (eq < 0) return;
    const key = declaration.slice(0, eq);
    const value = declaration.slice(eq + 1);
    try {
      localStorage.setItem(key, value);
    } catch (_err) {
      // Ignore — this session's dismissed state simply doesn't persist.
    }
  }

  document.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    const trigger = target.closest('[data-action="dismiss"]');
    if (!trigger) return;
    const overlay = /** @type {HTMLElement|null} */ (
      trigger.closest('[data-overlay]')
    );
    if (!overlay) return;

    hideOverlay(overlay);
    if (overlay.dataset.overlayPersist) persist(overlay.dataset.overlayPersist);

    overlay.dispatchEvent(
      new CustomEvent('overlay:dismissed', {
        bubbles: true,
        detail: { overlay: overlay },
      }),
    );
  });

  // ---------------------------------------------------------------------
  // Shared toast auto-dismiss controller.
  // ---------------------------------------------------------------------

  /** @type {WeakMap<Element, number>} */
  const timers = new WeakMap();

  /**
   * (Re)arm or clear a toast's auto-dismiss timer based on its current
   * visibility. Called on load and whenever the element's class/hidden
   * attribute changes, so a toast revealed at any point after page load
   * still gets its timer started.
   * @param {HTMLElement} el
   */
  function syncToastTimer(el) {
    const existing = timers.get(el);
    if (existing) {
      clearTimeout(existing);
      timers.delete(el);
    }
    if (isOverlayHidden(el)) return;
    const ms = parseInt(el.dataset.toastTimeout || '', 10);
    if (!ms) return;
    timers.set(
      el,
      setTimeout(function () {
        hideOverlay(el);
      }, ms),
    );
  }

  document.querySelectorAll('[data-toast-timeout]').forEach(function (el) {
    syncToastTimer(/** @type {HTMLElement} */ (el));
    new MutationObserver(function () {
      syncToastTimer(/** @type {HTMLElement} */ (el));
    }).observe(el, { attributes: true, attributeFilter: ['class', 'hidden'] });
  });
})();
