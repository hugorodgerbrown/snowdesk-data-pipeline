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
 *     auto-starts once, unless #home-intro is still showing — the identity
 *     card takes priority so the two overlays never stack on first paint;
 *     the tour remains reachable via the "?" roundel once it is dismissed.
 *   - Steps whose target selector resolves to nothing in the DOM (the
 *     flag-gated #favourite-add-btn / #report-btn controls) are skipped
 *     automatically when the active step list is built.
 *   - The "?" roundel (#map-help-toggle) re-opens the tour from step 1 at
 *     any time, regardless of stored state.
 *   - Back / Next / Skip control the sequence; Next becomes "Done" on the
 *     final step. Skip and Done both persist the dismissed state.
 *   - a11y: Escape closes (persists); Left/Right arrow move between steps;
 *     Tab/Shift+Tab are trapped within the tooltip's buttons while open;
 *     focus returns to #map-help-toggle (or whatever had focus before
 *     opening) on close.
 *   - prefers-reduced-motion disables the ring's pulse animation and the
 *     position transitions via a 'map-help--no-motion' class, mirroring
 *     home_intro.js's '--no-motion' pattern.
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
  const skipBtn = document.getElementById('map-help-skip');
  const toggleBtn = document.getElementById('map-help-toggle');

  if (
    !stepsList || !ring || !tooltip || !titleEl || !bodyEl ||
    !stepCountEl || !backBtn || !nextBtn || !skipBtn
  ) {
    // Malformed/partial markup — nothing sensible to drive. Fail silently
    // rather than throwing, matching home_intro.js's early-return guard.
    return;
  }

  const STORAGE_KEY = 'snowdesk.map.help';
  const DISMISSED_VALUE = 'seen';
  const RING_PADDING = 8;
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
   * filtering out any whose target selector matches nothing in the DOM
   * (the flag-gated favourite/report controls).
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
      .filter((step) => step.target && document.querySelector(step.target));
  };

  const steps = buildSteps();
  if (steps.length === 0) return;

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
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    render();
    tooltip.focus();
  };

  /**
   * Close the overlay and, when persist=true, record the dismissed state.
   *
   * @param {boolean} persist
   */
  const close = (persist) => {
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    if (persist) persistDismissed();
    if (lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    } else if (toggleBtn) {
      toggleBtn.focus();
    }
  };

  // ---- Control bindings ----

  backBtn.addEventListener('click', () => advance(-1));
  nextBtn.addEventListener('click', () => advance(1));
  skipBtn.addEventListener('click', () => close(true));

  if (toggleBtn) {
    // Re-opens from step 1 regardless of stored state — this is the
    // deliberate "show me again" affordance.
    toggleBtn.addEventListener('click', () => open(true));
  }

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
      // Focus trap: cycle within the tooltip's enabled buttons only.
      const focusable = Array.from(
        tooltip.querySelectorAll('button:not([disabled])'),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  // ---- First-run auto-start ----
  // Skips auto-start when #home-intro is present and still showing, so the
  // two overlays never stack on first paint — the identity card is
  // dismissed first, then the tour is reachable via the "?" roundel. This
  // also covers "#home-intro absent" (show_intro=False) and "#home-intro
  // already dismissed on a prior visit", both of which auto-start the tour.

  let persisted = null;
  try {
    persisted = localStorage.getItem(STORAGE_KEY);
  } catch (_) {
    // Private mode — treat as not yet seen.
  }

  if (persisted !== DISMISSED_VALUE) {
    const homeIntro = document.getElementById('home-intro');
    const homeIntroShowing = !!homeIntro && !homeIntro.hasAttribute('hidden');
    if (!homeIntroShowing) {
      open(true);
    }
  }
})();
