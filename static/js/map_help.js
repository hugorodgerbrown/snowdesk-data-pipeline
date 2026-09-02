/**
 * static/js/map_help.js — Map coachmark / help-overlay tour engine (SNOW-457).
 *
 * Drives the #map-help-overlay coachmark tour: a highlight ring plus a
 * tooltip card that walks a first-time visitor through the map's controls.
 * Step definitions are server-rendered as hidden <li data-help-target> nodes
 * inside #map-help-steps (all copy is translatable via {% trans %} in the
 * template) — this script only sequences and positions them.
 *
 * Behaviour:
 *   - On first load (localStorage key 'snowdesk.map.help' absent) the tour
 *     auto-starts once — unless #map-help-overlay carries
 *     data-map-help-no-autostart, an opt-out the embedding page sets when
 *     it has its own, better way to offer the tour. home.html sets it
 *     (SNOW-535): on the homepage the tour only opens via the "?" roundel
 *     or #home-intro's "Explore the map" CTA, never automatically straight
 *     after a visitor dismisses that card.
 *   - Steps whose target selector resolves to nothing in the DOM (the
 *     flag-gated #favourite-add-btn / #report-btn controls) are skipped
 *     automatically when the active step list is built.
 *   - The "?" roundel (#map-help-toggle) re-opens the tour from step 1 at
 *     any time, regardless of stored state — as does a
 *     'snowdesk:map-help-requested' CustomEvent, which home_intro.js
 *     dispatches when its "Explore the map" CTA is clicked.
 *   - Back / Next control the sequence; Next becomes "Done" on the final
 *     step. The "×" close button, Escape and Done all persist the dismissed
 *     state.
 *   - a11y: Escape closes (persists); Left/Right arrow move between steps;
 *     Tab/Shift+Tab are trapped within the tooltip's buttons while open;
 *     focus returns to #map-help-toggle (or whatever had focus before
 *     opening) on close.
 *   - prefers-reduced-motion disables the ring's pulse animation and the
 *     position transitions via a 'map-help--no-motion' class, mirroring
 *     home_intro.js's '--no-motion' pattern.
 *   - SNOW-658: the tour is a map overlay, so it registers with
 *     window.pwaMapOverlays (static/js/map_overlay_exclusivity.js) — opening
 *     it closes every other map overlay, and opening any of them closes it.
 *     Nothing in the walkthrough depends on another overlay staying open:
 *     every step points at map chrome, never at a panel's contents.
 *
 * Mirrors the IIFE + localStorage-guard shape of home_intro.js.
 */

(function mapHelpInit() {
  'use strict';

  const overlay = document.getElementById('map-help-overlay');
  if (!overlay) return;

  const stepsList = document.getElementById('map-help-steps');
  const ring = document.getElementById('map-help-ring');
  const tooltip = document.getElementById('map-help-tooltip');
  const titleEl = document.getElementById('map-help-tooltip-title');
  const bodyEl = document.getElementById('map-help-tooltip-body');
  const stepCountEl = document.getElementById('map-help-step-count');
  const stepTemplateEl = document.getElementById('map-help-step-template');
  const backBtn = document.getElementById('map-help-back');
  const nextBtn = document.getElementById('map-help-next');
  const closeBtn = document.getElementById('map-help-close');
  const toggleBtn = document.getElementById('map-help-toggle');

  if (
    !stepsList || !ring || !tooltip || !titleEl || !bodyEl ||
    !stepCountEl || !backBtn || !nextBtn || !closeBtn
  ) {
    // Malformed/partial markup — nothing sensible to drive. Fail silently
    // rather than throwing, matching home_intro.js's early-return guard.
    // (closeBtn is guarded for markup completeness only — its click is
    // handled by the shared overlays.js dismiss handler, not bound here;
    // see the Control bindings note below.)
    return;
  }

  const STORAGE_KEY = 'snowdesk.map.help';
  const DISMISSED_VALUE = 'seen';
  // SNOW-658: this surface's name in the shared map-overlay registry — the
  // element's own id, so a failing exclusivity assertion names something
  // greppable.
  const OVERLAY_NAME = 'map-help-overlay';
  const RING_PADDING = 4;
  const TOOLTIP_MARGIN = 12;

  // Respect prefers-reduced-motion: skip the ring pulse and the position
  // transitions, matching home_intro.js's equivalent guard.
  const prefersReducedMotion = window.matchMedia(
    '(prefers-reduced-motion: reduce)',
  ).matches;
  if (prefersReducedMotion) {
    overlay.classList.add('map-help--no-motion');
  }

  const stepTemplate = stepTemplateEl
    ? stepTemplateEl.textContent.trim()
    : 'Step {n} of {total}';
  const nextLabel = nextBtn.dataset.labelNext || nextBtn.textContent.trim();
  const doneLabel = nextBtn.dataset.labelDone || nextLabel;

  /**
   * Build the active step list from the server-rendered <li> nodes,
   * dropping any whose target is not on screen — either because the
   * selector matches nothing (the flag-gated favourite/report controls) or
   * because it matches an element CSS is not drawing.
   *
   * SNOW-794 added the second half. The season scrubber is ``display: none``
   * in two states — below 640px, and whenever the shown day is outside the
   * season — and in neither is it a control at all; existence alone would
   * have kept its step and pointed the ring at a zero box.
   *
   * ``checkVisibility()`` is the right question: it is false for
   * ``display: none`` on the element OR any ancestor, and TRUE for something
   * merely clipped — which matters, because the collapsible control group
   * shrinks to height 0 rather than hiding and its steps must survive so the
   * force-open below can reveal them. Where it is missing (older browsers,
   * and jsdom, which has no layout at all) the fallback reads the element's
   * own computed ``display``. That catches both cases this filter exists
   * for, since each is a rule on the scrubber itself.
   *
   * @returns {Array<{target: string, title: string, body: string}>}
   */
  const buildSteps = () => {
    const nodes = Array.from(stepsList.querySelectorAll('li[data-help-target]'));
    return nodes
      .map((li) => ({
        target: li.getAttribute('data-help-target') || '',
        title: li.getAttribute('data-help-title') || '',
        body: li.textContent.trim(),
      }))
      .filter((step) => {
        if (!step.target) return false;
        const el = document.querySelector(step.target);
        if (!el) return false;
        if (typeof el.checkVisibility === 'function') return el.checkVisibility();
        return getComputedStyle(el).display !== 'none';
      });
  };

  // Re-derived on every open (see ``open`` below), because what is on screen
  // is not fixed at parse time: the visitor can collapse the scrubber, and
  // the tour itself force-opens the control group a step before measuring it.
  let steps = buildSteps();

  let currentIndex = 0;
  let lastFocused = null;

  const isOpen = () => !overlay.hasAttribute('hidden');

  /**
   * Position the highlight ring over the target element's bounding rect.
   * Wide/short targets (the season ribbon, the scrubber) get a rounded-
   * rectangle ring; roughly-square controls (the 40×40 icon pills) get a
   * stadium/circle ring so it reads as "this button", not "this area".
   *
   * @param {Element} targetEl
   */
  const positionRing = (targetEl) => {
    const rect = targetEl.getBoundingClientRect();
    const top = rect.top - RING_PADDING;
    const left = rect.left - RING_PADDING;
    const width = rect.width + RING_PADDING * 2;
    const height = rect.height + RING_PADDING * 2;
    ring.style.top = `${top}px`;
    ring.style.left = `${left}px`;
    ring.style.width = `${width}px`;
    ring.style.height = `${height}px`;
    ring.style.borderRadius = width / height <= 3 ? '999px' : '14px';
  };

  /**
   * Position the tooltip card beside the target, flipping side (top,
   * bottom, left, right) when the preferred side has no room, then
   * clamping the result to stay fully on-screen.
   *
   * @param {Element} targetEl
   */
  const positionTooltip = (targetEl) => {
    const rect = targetEl.getBoundingClientRect();
    const ttRect = tooltip.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const fits = {
      top: rect.top - ttRect.height - TOOLTIP_MARGIN >= 0,
      bottom: rect.bottom + ttRect.height + TOOLTIP_MARGIN <= vh,
      left: rect.left - ttRect.width - TOOLTIP_MARGIN >= 0,
      right: rect.right + ttRect.width + TOOLTIP_MARGIN <= vw,
    };

    let side = 'bottom';
    if (fits.top) side = 'top';
    else if (fits.bottom) side = 'bottom';
    else if (fits.left) side = 'left';
    else if (fits.right) side = 'right';

    let top;
    let left;
    if (side === 'top') {
      top = rect.top - ttRect.height - TOOLTIP_MARGIN;
      left = rect.left + rect.width / 2 - ttRect.width / 2;
    } else if (side === 'bottom') {
      top = rect.bottom + TOOLTIP_MARGIN;
      left = rect.left + rect.width / 2 - ttRect.width / 2;
    } else if (side === 'left') {
      top = rect.top + rect.height / 2 - ttRect.height / 2;
      left = rect.left - ttRect.width - TOOLTIP_MARGIN;
    } else {
      top = rect.top + rect.height / 2 - ttRect.height / 2;
      left = rect.right + TOOLTIP_MARGIN;
    }

    // Clamp within the viewport so an edge-anchored control (e.g. the
    // zoom indicator, bottom-right) never pushes the card off-screen.
    left = Math.max(TOOLTIP_MARGIN, Math.min(left, vw - ttRect.width - TOOLTIP_MARGIN));
    top = Math.max(TOOLTIP_MARGIN, Math.min(top, vh - ttRect.height - TOOLTIP_MARGIN));

    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
    tooltip.dataset.side = side;
  };

  /**
   * Re-render the current step: tooltip copy, step counter, control
   * state, and the ring/tooltip position.
   */
  const render = () => {
    const step = steps[currentIndex];
    const targetEl = document.querySelector(step.target);
    if (!targetEl) {
      // Target vanished between build and render — skip forward rather
      // than pointing the ring at nothing.
      advance(1);
      return;
    }

    titleEl.textContent = step.title;
    bodyEl.textContent = step.body;
    stepCountEl.textContent = stepTemplate
      .replace('{n}', String(currentIndex + 1))
      .replace('{total}', String(steps.length));

    backBtn.disabled = currentIndex === 0;
    const isLast = currentIndex === steps.length - 1;
    nextBtn.textContent = isLast ? doneLabel : nextLabel;

    positionRing(targetEl);
    positionTooltip(targetEl);
    targetEl.scrollIntoView({
      block: 'nearest',
      behavior: prefersReducedMotion ? 'auto' : 'smooth',
    });
  };

  /** Recompute the ring/tooltip position for the current step, if open. */
  const reposition = () => {
    if (!isOpen()) return;
    const step = steps[currentIndex];
    const targetEl = document.querySelector(step.target);
    if (!targetEl) return;
    positionRing(targetEl);
    positionTooltip(targetEl);
  };

  /**
   * Move by `delta` steps, clamped to the sequence. Stepping past the
   * last step closes and persists the dismissed state (this is how the
   * last step's "Done" button behaves — it's the same Next button).
   *
   * @param {number} delta
   */
  const advance = (delta) => {
    const next = currentIndex + delta;
    if (next < 0) return;
    if (next >= steps.length) {
      close(true);
      return;
    }
    currentIndex = next;
    render();
  };

  /** Persist the dismissed flag, tolerating storage errors (private mode). */
  const persistDismissed = () => {
    try {
      localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    } catch (_) {
      // Private mode / storage full — dismissed state applies this
      // session only; the tour may auto-start again on a later visit.
    }
  };

  /**
   * Open the overlay and render the current (or first) step.
   *
   * @param {boolean} fromStart - Reset to step 0 before rendering.
   */
  const open = (fromStart) => {
    if (fromStart) currentIndex = 0;
    lastFocused = document.activeElement;
    // SNOW-658: only one overlay is open over the map at a time. Announced
    // before the tour is revealed, so the surface it replaces is gone by the
    // time the ring and tooltip are on screen. Safe for the walkthrough
    // itself: every step targets a piece of map CHROME — a roundel, the
    // readout, the scrubber, the date ribbon — and not one of them lives
    // inside another overlay, so nothing this closes is a step's target.
    // (#map-legend-toggle is the nearest miss: it is a SIBLING of the legend
    // card inside #map-legend, and stays on screen when the card collapses.)
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
    // Several step targets live in the collapsible control group, which clips
    // to height 0 when collapsed; since SNOW-794 the season scrubber can be
    // collapsed too, and it hides outright rather than clipping. Both still
    // match document.querySelector. Announce the tour so
    // mapControlsCollapseInit and mapScrubberCollapseInit can force their
    // surfaces open (and restore the user's preference on close).
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-open'));
    // Rebuild AFTER the force-open, not before: those two surfaces are now
    // drawn, so buildSteps measures what the visitor will actually see. At
    // parse time a collapsed scrubber would have been dropped from a tour
    // that is about to reveal it — and on a phone, where CSS drops the
    // scrubber for good, it is dropped here and stays dropped.
    steps = buildSteps();
    if (steps.length === 0) return;
    // Clamp rather than reset: `fromStart` above owns whether the tour
    // resumes where it left off, and the list can be SHORTER than it was on
    // the previous open (a resize across the breakpoint), which would leave
    // currentIndex past the end.
    currentIndex = Math.min(currentIndex, steps.length - 1);
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    render();
    tooltip.focus();
  };

  /**
   * Close the overlay and, when persist=true, record the dismissed state.
   *
   * @param {boolean} persist Record the tour as seen, so it stops
   *   auto-starting. True for the paths where the user ends the tour
   *   deliberately (Done, Escape, the "×"); false when something else takes
   *   the screen from it.
   * @param {boolean} [restoreFocus=true] Return focus to whatever held it
   *   when the tour opened. False when another overlay displaced the tour:
   *   focus belongs to the surface the user just opened, not to the control
   *   the tour borrowed it from.
   */
  const close = (persist, restoreFocus = true) => {
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    // Hand the collapsible group back to the user's own preference.
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-close'));
    if (persist) persistDismissed();
    if (!restoreFocus) return;
    if (lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    } else if (toggleBtn) {
      toggleBtn.focus();
    }
  };

  // ---- Control bindings ----

  backBtn.addEventListener('click', () => advance(-1));
  nextBtn.addEventListener('click', () => advance(1));
  // SNOW-486: closeBtn (#map-help-close) carries data-action="dismiss"
  // inside the [data-overlay] #map-help-overlay, so the click itself —
  // the hide (attribute idiom, unchanged) and the localStorage persist
  // (data-overlay-persist="snowdesk.map.help=seen") — is handled by
  // static/js/overlays.js's shared delegated handler below. No direct
  // click binding needed here.

  document.addEventListener('overlay:dismissed', (event) => {
    const dismissed = event.detail && event.detail.overlay;
    if (dismissed !== overlay) return;
    overlay.setAttribute('aria-hidden', 'true');
    if (lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    } else if (toggleBtn) {
      toggleBtn.focus();
    }
  });

  if (toggleBtn) {
    // Re-opens from step 1 regardless of stored state — this is the
    // deliberate "show me again" affordance.
    toggleBtn.addEventListener('click', () => open(true));
  }

  // SNOW-658: the tour closes whenever any other map overlay opens, and
  // closes every other one when it opens. It is non-modal by design (the
  // overlay is `pointer-events: none` so the map stays usable underneath),
  // which is precisely why it needed this: nothing stopped a user tapping a
  // roundel mid-tour and ending up with a panel and a floating tooltip card
  // fighting over the same corner.
  //
  // Closed without persisting: being displaced by another overlay is not the
  // user saying "I have seen this", which is what the Done / Escape / "×"
  // paths mean. The "?" roundel re-opens the tour at any time, and a tour
  // that was interrupted rather than finished is still worth auto-starting
  // on a page that has not opted out.
  window.pwaMapOverlays?.register(OVERLAY_NAME, {
    isOpen: isOpen,
    close: () => close(false, false),
  });

  // SNOW-535: the homepage's #home-intro "Explore the map" CTA asks for the
  // tour via this event (home_intro.js) rather than reaching into this
  // module's internals directly — keeps the tour-opening logic in one
  // place while decoupling the two scripts.
  document.addEventListener('snowdesk:map-help-requested', () => open(true));

  window.addEventListener('resize', reposition);
  window.addEventListener('scroll', reposition, true);

  // ---- Keyboard ----

  document.addEventListener('keydown', (e) => {
    if (!isOpen()) return;
    if (e.key === 'Escape') {
      close(true);
      return;
    }
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      advance(1);
      return;
    }
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      advance(-1);
      return;
    }
    if (e.key === 'Tab') {
      // Focus trap: cycle within the tooltip's enabled buttons only. Focus
      // starts on the tooltip container itself (it's the tabindex="-1"
      // element .focus()'d on open, before any button has been touched), so
      // Shift+Tab must also wrap from there — otherwise a keyboard user who
      // presses Shift+Tab first escapes the trap backward before ever
      // reaching a button.
      const focusable = Array.from(
        tooltip.querySelectorAll('button:not([disabled])'),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === tooltip)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  // ---- First-run auto-start ----
  // Skips auto-start when the embedding page opts out via
  // data-map-help-no-autostart on #map-help-overlay (home.html does this —
  // see the module docstring). Every other embedder keeps auto-starting.

  let persisted = null;
  try {
    persisted = localStorage.getItem(STORAGE_KEY);
  } catch (_) {
    // Private mode — treat as not yet seen.
  }

  if (
    persisted !== DISMISSED_VALUE &&
    !overlay.hasAttribute('data-map-help-no-autostart')
  ) {
    open(true);
  }
})();
