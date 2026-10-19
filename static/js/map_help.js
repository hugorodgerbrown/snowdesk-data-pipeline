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
 *     it has its own, better way to offer the tour. map.html sets it: there
 *     #map-tour-offer (static/js/map_tour_offer.js, SNOW-1117) asks once the
 *     map has settled, and its "Start the tour" opens the tour.
 *   - Steps whose target selector resolves to nothing in the DOM (the
 *     flag-gated #favourite-add-btn / #report-btn controls) are skipped
 *     automatically when the active step list is built.
 *   - SNOW-1118: seven steps in four chapters, about the map and not only
 *     its buttons. A step can point at nothing (the colours: no ring, the
 *     card at the foot of the screen), at several controls at once (a ring
 *     each), or at whichever of several alternatives is on screen
 *     ("#a | #b"). Its [data-help-extra] is cloned into the card. The
 *     progress bar draws a segment per step under its chapter's name, from
 *     data-help-chapter and the #map-help-chapters labels.
 *   - SNOW-1118: a step with data-help-action="show-region" selects a
 *     region itself, by dispatching 'snowdesk:map-help-show-region', which
 *     map.js answers (the region at the middle of the map, unless one is
 *     already selected). Selecting opens the region sheet on a phone, and
 *     that sheet's open would close the tour through window.pwaMapOverlays,
 *     so the tour ignores the registry's close while it dispatches. Leaving
 *     the step dispatches 'snowdesk:map-help-release-region' (map.js drops
 *     a selection only the tour made) and closes whatever it opened.
 *   - The "?" roundel (#map-help-toggle) is the way back in: it opens the
 *     tour from step 1, regardless of stored state — except where the page
 *     renders help mode (#map-help-mode, SNOW-1119), whose bar carries the
 *     tour instead. Between SNOW-535 and
 *     SNOW-1117 it opened the #home-intro welcome panel instead, which led
 *     into the tour by its own CTA; that panel is gone.
 *   - A 'snowdesk:map-help-requested' CustomEvent opens the tour from step 1
 *     too. map_tour_offer.js dispatches it from "Start the tour".
 *   - Back / Next control the sequence; Next becomes "Done" on the final
 *     step. The "×" close button, Escape and Done all persist the dismissed
 *     state.
 *   - a11y: Escape closes (persists); Left/Right arrow move between steps;
 *     Tab/Shift+Tab are trapped within the tooltip's buttons while open;
 *     focus returns to #map-help-toggle (or whatever had focus before
 *     opening) on close.
 *   - prefers-reduced-motion disables the ring's pulse animation and the
 *     position transitions via a 'map-help--no-motion' class, mirroring
 *     map_tour_offer.js's '--no-motion' pattern.
 *   - SNOW-658: the tour is a map overlay, so it registers with
 *     window.pwaMapOverlays (static/js/map_overlay_exclusivity.js) — opening
 *     it closes every other map overlay, and opening any of them closes it.
 *     Nothing in the walkthrough depends on another overlay staying open:
 *     every step points at map chrome, never at a panel's contents.
 *
 * An IIFE with a localStorage guard, the shape map_tour_offer.js shares.
 */

(function mapHelpInit() {
  'use strict';

  const overlay = document.getElementById('map-help-overlay');
  if (!overlay) return;

  const stepsList = document.getElementById('map-help-steps');
  const chaptersList = document.getElementById('map-help-chapters');
  const ring = document.getElementById('map-help-ring');
  const progressEl = document.getElementById('map-help-progress');
  const extraEl = document.getElementById('map-help-tooltip-extra');
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
    // rather than throwing, matching map_tour_offer.js's early-return guard.
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
  // How long an action step waits for what it opened (the region sheet's
  // slide up) before measuring it again.
  const ACTION_SETTLE_MS = 400;
  const SHOW_REGION_ACTION = 'show-region';

  // Respect prefers-reduced-motion: skip the ring pulse and the position
  // transitions, matching map_tour_offer.js's equivalent guard.
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
   * Whether CSS is drawing an element.
   *
   * SNOW-794: the season scrubber is ``display: none`` in two states — below
   * 640px, and whenever the shown day is outside the season — and in neither
   * is it a control at all; existence alone would have kept its step and
   * pointed the ring at a zero box.
   *
   * ``checkVisibility()`` is the right question: it is false for
   * ``display: none`` on the element OR any ancestor, and TRUE for something
   * merely clipped — which matters, because the collapsible control group
   * shrinks to height 0 rather than hiding and its steps must survive so the
   * force-open below can reveal them. Where it is missing (older browsers,
   * and jsdom, which has no layout at all) the fallback reads the element's
   * own computed ``display``.
   *
   * @param {Element} el
   * @returns {boolean}
   */
  const isDrawn = (el) => {
    if (el.hasAttribute('hidden')) return false;
    if (typeof el.checkVisibility === 'function') return el.checkVisibility();
    return getComputedStyle(el).display !== 'none';
  };

  /**
   * The elements a target spec names that are on screen now (SNOW-1118).
   *
   * ``"#a, #b"`` is every one of them (a ring each); ``"#a | #b"`` is the
   * first alternative that is drawn; ``""`` is none — a step about the map
   * itself.
   *
   * @param {string} spec
   * @returns {Element[]}
   */
  const resolveTargets = (spec) => {
    if (!spec) return [];
    if (spec.includes('|')) {
      for (const alt of spec.split('|')) {
        const el = document.querySelector(alt.trim());
        if (el && isDrawn(el)) return [el];
      }
      return [];
    }
    return spec
      .split(',')
      .map((sel) => document.querySelector(sel.trim()))
      .filter((el) => el && isDrawn(el));
  };

  /**
   * Build the active step list from the server-rendered <li> nodes,
   * dropping any whose target is not on screen — either because the
   * selector matches nothing (the flag-gated favourite/report controls) or
   * because it matches an element CSS is not drawing. Two kinds of step are
   * kept regardless: one with no target, which is about the map itself,
   * and one with an action, whose target appears only once the action runs
   * (the region sheet the tour opens).
   *
   * @returns {Array<{target: string, title: string, body: string,
   *   chapter: string, action: string, extra: Element|null}>}
   */
  const buildSteps = () => {
    const nodes = Array.from(stepsList.querySelectorAll(':scope > li'));
    return nodes
      .map((li) => {
        const bodyNode = li.querySelector('[data-help-body]');
        return {
          target: (li.getAttribute('data-help-target') || '').trim(),
          title: li.getAttribute('data-help-title') || '',
          body: (bodyNode || li).textContent.replace(/\s+/g, ' ').trim(),
          chapter: li.getAttribute('data-help-chapter') || '',
          action: li.getAttribute('data-help-action') || '',
          extra: li.querySelector('[data-help-extra]'),
        };
      })
      .filter((step) => {
        if (!step.target || step.action) return true;
        return resolveTargets(step.target).length > 0;
      });
  };

  /**
   * The chapter labels, keyed by chapter, in their template order.
   *
   * @returns {Map<string, string>}
   */
  const readChapters = () => {
    const labels = new Map();
    if (!chaptersList) return labels;
    chaptersList.querySelectorAll('li[data-chapter]').forEach((li) => {
      labels.set(li.getAttribute('data-chapter'), li.textContent.trim());
    });
    return labels;
  };
  const chapterLabels = readChapters();

  // Re-derived on every open (see ``open`` below), because what is on screen
  // is not fixed at parse time: the visitor can collapse the scrubber, and
  // the tour itself force-opens the control group a step before measuring it.
  let steps = buildSteps();

  let currentIndex = 0;
  let lastFocused = null;
  // The direction of the last move, so a step whose target has gone is
  // skipped the way the visitor was going rather than always forwards.
  let direction = 1;
  // True while the tour itself opens something (an action step): the
  // registry's close is the opened surface announcing itself, not another
  // overlay taking the screen.
  let acting = false;
  // The step index whose action has run and not yet been released.
  let actedIndex = -1;
  let settleTimer = null;
  // Rings beyond the first, for a step that points at several controls.
  const extraRings = [];

  const isOpen = () => !overlay.hasAttribute('hidden');

  /**
   * Position the highlight ring over the target element's bounding rect.
   * Wide/short targets (the season ribbon, the scrubber) get a rounded-
   * rectangle ring; roughly-square controls (the 40×40 icon pills) get a
   * stadium/circle ring so it reads as "this button", not "this area".
   *
   * @param {Element} targetEl
   */
  const positionRing = (targetEl, ringEl = ring) => {
    const rect = targetEl.getBoundingClientRect();
    // Kept on screen: a target flush with an edge (the phone's region
    // sheet spans the width and sits on the bottom) would otherwise have
    // its ring drawn off-screen on three sides.
    const edge = RING_PADDING / 2;
    const top = Math.max(edge, rect.top - RING_PADDING);
    const left = Math.max(edge, rect.left - RING_PADDING);
    const width = Math.min(window.innerWidth - edge, rect.right + RING_PADDING) - left;
    const height = Math.min(window.innerHeight - edge, rect.bottom + RING_PADDING) - top;
    ringEl.style.top = `${top}px`;
    ringEl.style.left = `${left}px`;
    ringEl.style.width = `${width}px`;
    ringEl.style.height = `${height}px`;
    ringEl.style.borderRadius = width / height <= 3 ? '999px' : '14px';
  };

  /**
   * Draw a ring round each target: the first is #map-help-ring, the rest
   * are made as needed and hidden when a step needs fewer. No target hides
   * them all.
   *
   * @param {Element[]} targets
   */
  const positionRings = (targets) => {
    ring.hidden = targets.length === 0;
    if (targets.length) positionRing(targets[0]);
    for (let i = 1; i < targets.length; i += 1) {
      if (!extraRings[i - 1]) {
        const el = document.createElement('div');
        el.className = 'map-help-ring';
        el.setAttribute('aria-hidden', 'true');
        overlay.insertBefore(el, tooltip);
        extraRings.push(el);
      }
      extraRings[i - 1].hidden = false;
      positionRing(targets[i], extraRings[i - 1]);
    }
    extraRings.slice(Math.max(0, targets.length - 1)).forEach((el) => {
      el.hidden = true;
    });
  };

  /**
   * Place the card for a step with no target: across the foot of the
   * screen, clear of its edge, where it leaves the map it talks about in
   * view.
   */
  const positionTooltipFree = () => {
    const ttRect = tooltip.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    tooltip.style.left = `${Math.max(TOOLTIP_MARGIN, (vw - ttRect.width) / 2)}px`;
    tooltip.style.top = `${Math.max(TOOLTIP_MARGIN, vh - ttRect.height - TOOLTIP_MARGIN * 2)}px`;
    tooltip.dataset.side = 'none';
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
   * Draw the progress bar: a segment per step, grouped under its chapter's
   * name, the steps before this one filled. Hidden, and the plain step
   * count shown instead, when the steps carry no chapters.
   */
  const renderProgress = () => {
    const chaptered = progressEl && steps.every((step) => chapterLabels.has(step.chapter));
    stepCountEl.classList.toggle('map-help-visually-hidden', !!chaptered);
    if (!progressEl) return;
    progressEl.hidden = !chaptered;
    if (!chaptered) return;
    const current = steps[currentIndex].chapter;
    progressEl.replaceChildren();
    chapterLabels.forEach((label, chapter) => {
      const indices = steps
        .map((step, i) => (step.chapter === chapter ? i : -1))
        .filter((i) => i !== -1);
      if (indices.length === 0) return;
      const group = document.createElement('div');
      group.className = 'map-help-progress-chapter';
      group.style.flexGrow = String(indices.length);
      group.dataset.state = chapter === current ? 'current' : 'other';
      const bar = document.createElement('div');
      bar.className = 'map-help-progress-segments';
      indices.forEach((i) => {
        const seg = document.createElement('span');
        seg.className = 'map-help-progress-segment';
        seg.dataset.state = i <= currentIndex ? 'done' : 'todo';
        bar.append(seg);
      });
      const name = document.createElement('span');
      name.className = 'map-help-progress-label';
      name.textContent = label;
      group.append(bar, name);
      progressEl.append(group);
    });
  };

  /**
   * Put the step's [data-help-extra] (the scale, the four lists) under its
   * copy, or hide the slot. A clone, so the template's copy stays whole for
   * the next open.
   *
   * @param {Element|null} extra
   */
  const renderExtra = (extra) => {
    if (!extraEl) return;
    extraEl.replaceChildren(...(extra ? Array.from(extra.cloneNode(true).childNodes) : []));
    extraEl.hidden = !extra;
  };

  /**
   * Let go of whatever an action step opened: the region the tour selected
   * (map.js drops it only if the tour made it) and the surface showing it.
   */
  const releaseAction = () => {
    if (actedIndex === -1) return;
    actedIndex = -1;
    clearTimeout(settleTimer);
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-release-region'));
    // A region the visitor had selected themselves stays selected, but the
    // sheet the tour opened for it would cover the next steps' controls.
    if (isOpen()) {
      acting = true;
      try {
        window.pwaMapOverlays?.opening(OVERLAY_NAME);
      } finally {
        acting = false;
      }
    }
  };

  /** Run the current step's action once, when the step is reached. */
  const runAction = (step) => {
    if (step.action !== SHOW_REGION_ACTION || actedIndex === currentIndex) return;
    actedIndex = currentIndex;
    // The sheet takes focus as it opens; hand it back to the card, so a
    // keyboard user's next Enter is still the tour's Next.
    const focused = document.activeElement;
    acting = true;
    try {
      document.dispatchEvent(new CustomEvent('snowdesk:map-help-show-region'));
    } finally {
      acting = false;
    }
    if (focused && tooltip.contains(focused)) focused.focus();
    // The sheet slides up; measure it again as it rises and once it has.
    clearTimeout(settleTimer);
    const settle = (delay) => {
      settleTimer = setTimeout(() => {
        reposition();
        if (delay < ACTION_SETTLE_MS) settle(delay * 2);
      }, delay);
    };
    settle(ACTION_SETTLE_MS / 4);
  };

  /**
   * Re-render the current step: tooltip copy, step counter, progress,
   * control state, and the ring/tooltip position.
   */
  const render = () => {
    const step = steps[currentIndex];
    if (actedIndex !== -1 && actedIndex !== currentIndex) releaseAction();
    runAction(step);
    const targets = resolveTargets(step.target);
    if (step.target && targets.length === 0) {
      // Target vanished between build and render (or the action found
      // nothing to open) — skip on rather than pointing the ring at nothing.
      advance(direction);
      return;
    }

    titleEl.textContent = step.title;
    bodyEl.textContent = step.body;
    renderExtra(step.extra);
    stepCountEl.textContent = stepTemplate
      .replace('{n}', String(currentIndex + 1))
      .replace('{total}', String(steps.length));
    renderProgress();

    backBtn.disabled = currentIndex === 0;
    const isLast = currentIndex === steps.length - 1;
    nextBtn.textContent = isLast ? doneLabel : nextLabel;

    place(targets);
    if (targets.length) {
      targets[0].scrollIntoView({
        block: 'nearest',
        behavior: prefersReducedMotion ? 'auto' : 'smooth',
      });
    }
  };

  /**
   * Ring the targets and set the card beside the first, or at the foot of
   * the screen for a step with none.
   *
   * @param {Element[]} targets
   */
  const place = (targets) => {
    positionRings(targets);
    if (targets.length) positionTooltip(targets[0]);
    else positionTooltipFree();
  };

  /** Recompute the ring/tooltip position for the current step, if open. */
  const reposition = () => {
    if (!isOpen()) return;
    const step = steps[currentIndex];
    const targets = resolveTargets(step.target);
    if (step.target && targets.length === 0) return;
    place(targets);
  };

  /**
   * Move by `delta` steps, clamped to the sequence. Stepping past the
   * last step closes and persists the dismissed state (this is how the
   * last step's "Done" button behaves — it's the same Next button).
   *
   * @param {number} delta
   */
  const advance = (delta) => {
    direction = delta < 0 ? -1 : 1;
    const next = currentIndex + delta;
    if (next < 0) {
      // Back from the first step found nothing to show: stay put, or close
      // if this step cannot be shown either.
      if (resolveTargets(steps[currentIndex].target).length || !steps[currentIndex].target) return;
      close(false);
      return;
    }
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
    direction = 1;
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
    releaseAction();
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
    releaseAction();
    overlay.setAttribute('aria-hidden', 'true');
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-close'));
    if (lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    } else if (toggleBtn) {
      toggleBtn.focus();
    }
  });

  // The deliberate "show me again" affordance — on a page without help
  // mode. Where #map-help-mode is rendered (map.html, SNOW-1119) the "?"
  // turns help mode on instead, and the tour is its bar's "Tour" button;
  // map_help_mode.js takes the roundel's click in the capture phase.
  if (toggleBtn && !document.getElementById('map-help-mode')) {
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
    close: () => {
      if (acting) return;
      close(false, false);
    },
  });

  // SNOW-1117: #map-tour-offer's "Start the tour" asks for the tour via this
  // event (map_tour_offer.js) rather than reaching into this module's
  // internals — the tour-opening logic stays in one place.
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
