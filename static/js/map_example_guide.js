/*
 * static/js/map_example_guide.js — the example route walkthrough (SNOW-1230).
 *
 * The welcome card's "Explore an example route" goes to routes:example,
 * which puts the example share in the session and lands on the map with
 * ?route_share=<token>&example=1&d=<the example's day>. map.js opens the
 * route as it opens any followed share; this module runs the guide over it
 * (public/partials/_map_example_guide.html renders every word). The steps
 * and their tests are example_guide_core.js:
 *
 *   intro    say what just happened to the date; Got it.
 *   drag     move the point along the profile (the rail's cursor).
 *   zoom     zoom in until the line draws in slope classes.
 *   colours  what the colours mean; Finish.
 *
 * NOTHING IS LOCKED (Hugo, SNOW-1230). The guide is not a map overlay and
 * does not register with window.pwaMapOverlays: the map is the lesson, so
 * every control keeps working. Two things fold it into the paused pill
 * (#map-example-pill), which keeps its step:
 *
 *   - the example's route is not the one open in the rail (closed, or
 *     another route opened) — the pill offers Resume, which reopens it;
 *   - the map is on another day — the pill offers the way back to the
 *     example's day, and the "Example day" badge comes off the date,
 *     because the badge never sits on a day the example did not choose.
 *
 * LEAVING ALWAYS PUTS THE MAP BACK ON TODAY, AND SAYS SO. Exit (from the bar
 * or the pill) and Finish both: drop the example from the session
 * (routes:share_dismiss — it stops being drawn and listed), close the rail,
 * commit today through 'snowdesk:scrub-to' as map_today.js does, and toast
 * "Back to today · <date>". Finish then shows the finish card.
 *
 * Telemetry: map.example.opened, map.example.step_completed {step},
 * map.example.exited {step}, map.example.finished. A finished or exited walk
 * is remembered on the device ('snowdesk.example.walked'), so routes.js
 * can mark an upload that follows it.
 *
 * Depends on example_guide_core.js, i18n_strings.js, route_rail.js
 * (window.pwaRouteRail) and map.js (window.pwaRouteOpener,
 * window.snowdeskMapState), all loaded before it; overlays.js times out
 * the toast.
 */
(function mapExampleGuideInit() {
  'use strict';

  const guide = document.getElementById('map-example-guide');
  const pill = document.getElementById('map-example-pill');
  const finishCard = document.getElementById('map-example-finish');
  const mapEl = document.getElementById('map');
  const core = window.pwaExampleGuideCore;
  if (!guide || !pill || !finishCard || !mapEl || !core) return;

  // Only the example link starts the guide; the parameter goes at once, so
  // a reload or a shared address does not start it again.
  if (!core.isExampleArrival(location.search)) return;
  const params = new URLSearchParams(location.search);
  // Read before map.js strips it, which it does from the map's load event —
  // after every deferred script has run.
  const exampleToken = params.get('route_share');
  const exampleDate = params.get('d') || '';
  history.replaceState(
    null,
    '',
    location.pathname + core.withoutExampleParam(location.search) + location.hash,
  );
  if (!exampleToken) return;

  const STRINGS = self.pwaStrings.read('map-example-guide-strings-template', {
    'step-of': 'Example route · %(n)s of %(count)s',
    'back-to-day': 'Back to %(date)s',
    'back-to-today': 'Back to today · %(date)s',
  });
  const locale = document.documentElement.lang || undefined;
  const scrubber = document.getElementById('season-scrubber');
  const today = (scrubber && scrubber.dataset.today) || '';
  const badge = document.getElementById('map-example-day-badge');
  const toast = document.getElementById('map-example-toast');
  const eyebrow = document.getElementById('map-example-guide-eyebrow');
  const zoomFill = /** @type {HTMLElement|null} */ (guide.querySelector('[data-example-zoom-fill]'));
  const dots = Array.from(guide.querySelectorAll('.map-example-guide-dots li'));
  const DISMISS_URL_TEMPLATE = mapEl.dataset.routeShareDismissUrlTemplate || '';
  const ZOOM_TARGET = (window.pwaRouteDrawingCore && window.pwaRouteDrawingCore.ROUTE_SLOPE_MINZOOM) || 14;
  const WALKED_KEY = 'snowdesk.example.walked';
  // A beat between a step done and the next, so the reader sees what they
  // did land (the dot travelled, the line changed colour).
  const STEP_PAUSE_MS = 700;
  const GAP = 8;

  /** @type {'waiting'|'running'|'ended'} */
  let phase = 'waiting';
  /** @type {string} */
  let step = exampleDate ? 'intro' : 'drag';
  let date = exampleDate || today;
  let railShowsExample = false;
  let unsubscribeCursor = null;
  let drag = null;
  let zoomStart = NaN;
  let stepTimer = null;

  const emit = (name, props) => window.pwaTelemetry?.emit(name, props || {});

  const onExampleDay = () => !exampleDate || date === exampleDate;

  // ---- the words that are filled in ----

  const dayText = core.formatDay(exampleDate, locale);
  guide.querySelectorAll('[data-example-day]').forEach((el) => {
    el.textContent = dayText;
  });
  const backToDay = pill.querySelector('[data-example-back-to-day]');
  if (backToDay) {
    backToDay.textContent = self.pwaStrings.interpolate(STRINGS['back-to-day'], {
      date: core.formatShortDay(exampleDate, locale),
    });
  }

  /** Name the route in the drag step, from the rail it is open in. */
  const fillRouteName = () => {
    const rail = window.pwaRouteRail && window.pwaRouteRail.element;
    const name = rail && rail.querySelector('[data-route-rail-name]');
    const text = name ? name.textContent.trim() : '';
    guide.querySelectorAll('[data-example-name]').forEach((el) => {
      el.textContent = text;
    });
  };

  // ---- placing the bar and the pill ----

  /**
   * Put a floating element beside the route it talks about: on a desktop
   * under the route panel (a selection's column); on a phone on top of the
   * route's sheet, so the words sit over the profile they point at and
   * ride up and down with it; with no route open, under the controls along
   * the top edge. Never above those controls.
   *
   * @param {HTMLElement} el
   */
  const place = (el) => {
    const mapTop = mapEl.getBoundingClientRect().top;
    const cluster = document.getElementById('map-utility-cluster');
    const floor = (cluster ? cluster.getBoundingClientRect().bottom : mapTop) - mapTop + GAP;
    const rail = window.pwaRouteRail;
    const railEl = rail && rail.element;
    let top = floor;
    if (railEl && rail.isOpen()) {
      const box = railEl.getBoundingClientRect();
      if (rail.isSheet && rail.isSheet()) top = box.top - mapTop - el.offsetHeight - GAP;
      else top = box.bottom - mapTop + GAP;
    }
    el.style.top = `${Math.round(Math.max(floor, top))}px`;
  };

  // A phone's sheet moves between detents with no event of its own, so the
  // bar follows it frame by frame while it shows — help mode's approach to
  // the same problem (map_help_mode.js).
  let frame = 0;
  const follow = () => {
    cancelAnimationFrame(frame);
    if (guide.hidden && pill.hidden) return;
    if (!guide.hidden) place(guide);
    if (!pill.hidden) place(pill);
    frame = requestAnimationFrame(follow);
  };
  const placeAll = follow;

  // ---- painting ----

  /** Show the current step's text, count and dots. */
  const paintStep = () => {
    guide.querySelectorAll('[data-example-step]').forEach((el) => {
      el.hidden = el.getAttribute('data-example-step') !== step;
    });
    const n = core.stepNumber(step);
    if (eyebrow) {
      eyebrow.textContent = self.pwaStrings.interpolate(STRINGS['step-of'], {
        n: String(n),
        count: String(core.STEPS.length),
      });
    }
    dots.forEach((dot, i) => dot.classList.toggle('is-on', i < n));
  };

  /** Bar, pill or neither; the badge on the date while it is the example's. */
  const paint = () => {
    const active = phase === 'running';
    const showBar = active && railShowsExample && onExampleDay();
    guide.hidden = !showBar;
    pill.hidden = !active || showBar;
    if (active && !showBar) {
      const offDay = !onExampleDay();
      pill.querySelector('[data-example-pill="paused"]').hidden = offDay;
      pill.querySelector('[data-example-pill="off-day"]').hidden = !offDay;
      pill.querySelector('[data-example-resume]').hidden = offDay;
      if (backToDay) backToDay.hidden = !offDay;
    }
    if (badge) badge.hidden = !(active && exampleDate && onExampleDay());
    if (showBar) paintStep();
    placeAll();
  };

  // ---- the steps ----

  /** The open rail's cursor, followed for the drag step. */
  const followCursor = () => {
    if (unsubscribeCursor) unsubscribeCursor();
    unsubscribeCursor = null;
    const rail = window.pwaRouteRail;
    const cursor = rail && rail.cursor();
    if (!cursor || !railShowsExample) return;
    drag = core.createDragTracker(rail.sampleCount ? rail.sampleCount() : 0);
    unsubscribeCursor = cursor.subscribe((state) => {
      if (phase !== 'running' || step !== 'drag') return;
      if (drag.record(state.index)) completeStep();
    });
  };

  const theMap = () => window.snowdeskMapState && window.snowdeskMapState.map;

  /** Fill the zoom bar, and move on once the line shows the slope. */
  const onZoom = () => {
    if (phase !== 'running' || step !== 'zoom') return;
    const map = theMap();
    if (!map) return;
    const zoom = map.getZoom();
    const fill = core.zoomProgress(zoom, zoomStart, ZOOM_TARGET);
    if (zoomFill) zoomFill.style.width = `${Math.round(fill * 100)}%`;
    if (fill >= 1) completeStep();
  };

  /**
   * Begin `next`. Each step arms what it waits for.
   *
   * @param {string} next
   */
  const enterStep = (next) => {
    step = next;
    if (step === 'drag' && drag) drag.reset();
    if (step === 'zoom') {
      const map = theMap();
      zoomStart = map ? map.getZoom() : NaN;
      if (zoomFill) zoomFill.style.width = '0%';
    }
    paint();
    if (step === 'zoom') onZoom();
  };

  /** The current step is done: say so, and after a beat, the next. */
  function completeStep() {
    if (stepTimer !== null) return;
    emit('map.example.step_completed', { step: step });
    const done = step;
    stepTimer = setTimeout(() => {
      stepTimer = null;
      if (phase !== 'running' || step !== done) return;
      const next = core.nextStep(done);
      if (next) enterStep(next);
    }, done === 'intro' ? 0 : STEP_PAUSE_MS);
  }

  // ---- leaving ----

  /** Read the CSRF token the page carries for its other POSTs. */
  const csrfToken = () => {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input instanceof HTMLInputElement && input.value) return input.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  };

  /**
   * Stop the guide and put the map back as it was before the example:
   * the example dropped from this session, the rail closed, today shown.
   */
  const end = () => {
    phase = 'ended';
    if (stepTimer !== null) clearTimeout(stepTimer);
    stepTimer = null;
    if (unsubscribeCursor) unsubscribeCursor();
    unsubscribeCursor = null;
    paint();
    try {
      localStorage.setItem(WALKED_KEY, 'yes');
    } catch (_) {
      // Private mode: the mark holds for nothing, which costs a property.
    }

    if (railShowsExample) window.pwaRouteRail?.close();
    if (DISMISS_URL_TEMPLATE) {
      fetch(DISMISS_URL_TEMPLATE.replace('__TOKEN__', encodeURIComponent(exampleToken)), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() },
      })
        .catch(() => null)
        .then(() => {
          // Redraw without it, whatever the answer: a failed drop leaves
          // the line drawn, which is the state the visitor already had.
          document.dispatchEvent(new CustomEvent('snowdesk:routes-changed', { detail: {} }));
        });
    }
    if (today && date !== today) {
      document.dispatchEvent(new CustomEvent('snowdesk:scrub-to', {
        detail: { date: today, source: 'example' },
      }));
    }
    if (today && toast) {
      const body = toast.querySelector('[data-toast-body]');
      if (body) {
        body.textContent = self.pwaStrings.interpolate(STRINGS['back-to-today'], {
          date: core.formatShortDay(today, locale),
        });
      }
      // overlays.js arms the toast's own timeout as it shows.
      toast.classList.remove('hidden');
    }
  };

  const exit = () => {
    if (phase !== 'running') return;
    emit('map.example.exited', { step: step });
    end();
  };

  const finish = () => {
    if (phase !== 'running') return;
    emit('map.example.step_completed', { step: step });
    emit('map.example.finished', {});
    end();
    finishCard.hidden = false;
  };

  const hideFinish = () => {
    finishCard.hidden = true;
  };

  // ---- wiring ----

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest('[data-example-exit]')) exit();
    else if (target.closest('[data-example-next]') && step === 'intro') completeStep();
    else if (target.closest('[data-example-finish]')) finish();
    else if (target.closest('[data-example-resume]')) resume();
    else if (target.closest('[data-example-back-to-day]')) goToExampleDay();
    else if (target.closest('[data-example-done]')) hideFinish();
    else if (target.closest('[data-example-upload]')) {
      hideFinish();
      document.getElementById('route-add-btn')?.click();
    } else if (target.closest('[data-example-resort]')) {
      hideFinish();
      // SNOW-1231: the resort walkthrough, as the welcome card starts it.
      document.dispatchEvent(new CustomEvent('snowdesk:resort-guide-requested'));
    }
  });

  /** Commit the example's day, as the calendar would. */
  function goToExampleDay() {
    if (!exampleDate) return;
    document.dispatchEvent(new CustomEvent('snowdesk:scrub-to', {
      detail: { date: exampleDate, source: 'example' },
    }));
  }

  /** Reopen the example's route, keeping a close-up past the zoom step. */
  function resume() {
    if (phase !== 'running') return;
    const keepCamera = step === 'zoom' || step === 'colours';
    window.pwaRouteOpener?.openShare(exampleToken, { frame: !keepCamera });
  }

  // The rail opened, closed or changed route.
  document.addEventListener('snowdesk:route-rail-changed', () => {
    if (phase === 'ended') return;
    const rail = window.pwaRouteRail;
    railShowsExample = Boolean(rail && rail.isOpen() && rail.token() === exampleToken);
    if (railShowsExample) {
      fillRouteName();
      if (phase === 'waiting') {
        phase = 'running';
        emit('map.example.opened', {});
        enterStep(step);
        // The route was framed before the bar appeared over it.
        window.pwaRouteOpener?.frame(exampleToken);
      }
      followCursor();
    }
    paint();
  });

  document.addEventListener('snowdesk:date-changed', (event) => {
    const detail = /** @type {CustomEvent} */ (event).detail;
    if (!detail || !detail.date) return;
    date = detail.date;
    if (phase === 'running') paint();
  });

  document.addEventListener('snowdesk:route-rail-resized', placeAll);
  window.addEventListener('resize', placeAll);

  // The zoom step listens to the camera once there is one.
  const bindZoom = () => {
    const map = theMap();
    if (!map) return false;
    map.on('zoom', onZoom);
    return true;
  };
  if (!bindZoom()) {
    Promise.resolve(window.snowdeskMapState && window.snowdeskMapState.ready)
      .then(bindZoom)
      .catch(() => null);
  }
}());
