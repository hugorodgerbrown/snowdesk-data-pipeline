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
 * Escape and its "×" close it too.
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
    if (target.closest('[data-help-menu-close]')) {
      close();
      toggle.focus();
      return;
    }
    const item = target.closest('[data-help-menu]');
    if (!item) return;
    const choice = item.getAttribute('data-help-menu');
    window.pwaTelemetry?.emit('map.help_menu.chosen', { choice: choice });
    // The help pages item is a link: it closes as the page goes.
    close();
    if (choice === 'welcome') {
      document.dispatchEvent(new CustomEvent('snowdesk:map-welcome-requested'));
    } else if (choice === 'help-mode') {
      document.dispatchEvent(new CustomEvent('snowdesk:map-help-mode-requested'));
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !isOpen()) return;
    close();
    toggle.focus();
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen: isOpen, close: close });
}());
