/*
 * static/js/map_help_menu.js — the "?" roundel's menu (SNOW-1230).
 *
 * The "?" (#map-help-toggle) turned help mode on (SNOW-1119). It opens
 * #map-help-menu now (public/partials/_map_help_menu.html), so the welcome
 * a first visit offers is always one press away:
 *
 *   Show me around again           → 'snowdesk:map-welcome-requested',
 *                                    which map_tour_offer.js answers.
 *   What is everything on the map?  → 'snowdesk:map-help-mode-requested',
 *                                    which map_help_mode.js answers.
 *   Test 3D on this device         → 'snowdesk:map-3d-test-requested',
 *                                    which map_3d_test.js answers
 *                                    (SNOW-1456; only with the 3D view).
 *   Tune the relief                → 'snowdesk:map-relief-tuning-requested',
 *                                    which map_relief_tuning.js answers
 *                                    (staff only, past the border).
 *   Map key                        → the (i) card (window.pwaMapLegend),
 *                                    in the installed phone app only,
 *                                    which hides that roundel.
 *   Help pages                     → a link to /help/.
 *
 * HELP MODE KEEPS THE ROUNDEL'S PRESS. map_help_mode.js takes every click
 * on the "?" in the capture phase, so nothing else bound to it ever runs;
 * while help mode is off and this menu is on the page, it dispatches
 * 'snowdesk:map-help-menu-requested' instead of starting, and this module
 * toggles the menu. While help mode is on, the "?" ends it, as before.
 *
 * A map overlay like the welcome card it shares a corner with, so it
 * registers with window.pwaMapOverlays: opening anything else closes it.
 * Escape and its "×" close it too: the corner × from 640px
 * (`data-help-menu-close`), and on a phone the header's
 * (`data-action="help-menu-close"`, includes/_sheet_header.html).
 *
 * ON A PHONE IT IS THE SHEETS' CARD (SNOW-1486), with a grab handle in its
 * header (`data-help-menu-drag`). A drag down there closes it at the same
 * distance a Saved sheet closes from half (map_sheet.js's settle, through
 * window.pwaSheetDetentsCore when it is on the page): the card follows the
 * finger, and on release it either closes or springs back.
 */
(function mapHelpMenuInit() {
  'use strict';

  const menu = document.getElementById('map-help-menu');
  const toggle = document.getElementById('map-help-toggle');
  if (!menu || !toggle) return;

  const OVERLAY_NAME = 'map-help-menu';

  const isOpen = () => !menu.hidden;

  const close = () => {
    if (!isOpen()) return;
    menu.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  };

  const open = () => {
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
    menu.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    const first = menu.querySelector('[data-help-menu]');
    if (first instanceof HTMLElement) first.focus();
  };

  toggle.setAttribute('aria-controls', 'map-help-menu');
  toggle.setAttribute('aria-expanded', 'false');

  document.addEventListener('snowdesk:map-help-menu-requested', () => {
    if (isOpen()) close();
    else open();
  });

  menu.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest('[data-help-menu-close], [data-action="help-menu-close"]')) {
      close();
      toggle.focus();
      return;
    }
    const item = target.closest('[data-help-menu]');
    if (!item) return;
    const choice = item.getAttribute('data-help-menu');
    window.pwaTelemetry?.emit('map.help_menu.chosen', { choice: choice });
    // The demo and help pages items are links: they close as the page goes.
    close();
    if (choice === 'welcome') {
      document.dispatchEvent(new CustomEvent('snowdesk:map-welcome-requested'));
    } else if (choice === 'help-mode') {
      document.dispatchEvent(new CustomEvent('snowdesk:map-help-mode-requested'));
    } else if (choice === 'test-3d') {
      document.dispatchEvent(new CustomEvent('snowdesk:map-3d-test-requested'));
    } else if (choice === 'relief-tuning') {
      document.dispatchEvent(new CustomEvent('snowdesk:map-relief-tuning-requested'));
    } else if (choice === 'key') {
      // After this click has finished bubbling: the card's own outside-tap
      // dismiss would read it as a tap away and close what just opened.
      window.setTimeout(() => window.pwaMapLegend?.open(), 0);
    }
  });

  // ---------------------------------------------------------------------
  // A drag down on the phone header closes it (SNOW-1486).
  // ---------------------------------------------------------------------

  // The fraction of its height a card must keep to stay open, and the
  // fling projection: sheet_detents_core.js's, which map_sheet.js uses.
  const CLOSE_FRACTION_FALLBACK = 0.5;
  const PROJECTION_MS_FALLBACK = 150;
  // A pointer that travels less than this is a press, not a drag.
  const DRAG_SLOP_PX = 4;

  /** @type {{ id: number, startY: number, height: number, moved: boolean, lastY: number, lastT: number, velocity: number }|null} */
  let drag = null;

  /** @returns {HTMLElement|null} The card that moves under the finger. */
  const card = () => menu.querySelector('.map-tour-offer-card');

  /**
   * Whether a drag that leaves the card `height` px tall, moving at
   * `velocity` px/ms (positive grows it), closes it.
   *
   * @param {number} startHeight
   * @param {number} height
   * @param {number} velocity
   * @returns {boolean}
   */
  const closes = (startHeight, height, velocity) => {
    const core = window.pwaSheetDetentsCore;
    if (core && typeof core.settle === 'function') {
      return core.settle({
        detents: ['half'],
        heights: { half: startHeight },
        height: height,
        velocity: velocity,
      }) === 'close';
    }
    const projected = height + velocity * PROJECTION_MS_FALLBACK;
    return projected < startHeight * CLOSE_FRACTION_FALLBACK;
  };

  const release = () => {
    const el = card();
    if (el) el.style.removeProperty('transform');
    drag = null;
  };

  menu.addEventListener('pointerdown', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target || !target.closest('[data-help-menu-drag]')) return;
    if (target.closest('button, a')) return;
    const el = card();
    if (!el) return;
    drag = {
      id: event.pointerId,
      startY: event.clientY,
      height: el.getBoundingClientRect().height,
      moved: false,
      lastY: event.clientY,
      lastT: event.timeStamp || Date.now(),
      velocity: 0,
    };
  });

  menu.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const down = Math.max(0, event.clientY - drag.startY);
    if (!drag.moved && down < DRAG_SLOP_PX) return;
    drag.moved = true;
    const now = event.timeStamp || Date.now();
    const elapsed = now - drag.lastT;
    // Positive velocity grows the card, as the detent core reads it.
    if (elapsed > 0) drag.velocity = (drag.lastY - event.clientY) / elapsed;
    drag.lastY = event.clientY;
    drag.lastT = now;
    const el = card();
    if (el) el.style.transform = 'translateY(' + down + 'px)';
    event.preventDefault();
  });

  /** @param {PointerEvent} event */
  const endDrag = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const finished = drag;
    release();
    if (!finished.moved) return;
    const down = Math.max(0, event.clientY - finished.startY);
    const velocity = event.type === 'pointercancel' ? 0 : finished.velocity;
    if (closes(finished.height, finished.height - down, velocity)) close();
  };

  menu.addEventListener('pointerup', endDrag);
  menu.addEventListener('pointercancel', endDrag);

  // The installed phone app's Help tab opens this same menu
  // (static/js/app_tabbar.js), where the "?" roundel is hidden.
  window.pwaMapHelpMenu = Object.freeze({ open: open, close: close, isOpen: isOpen });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !isOpen()) return;
    close();
    toggle.focus();
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen: isOpen, close: close });
}());
