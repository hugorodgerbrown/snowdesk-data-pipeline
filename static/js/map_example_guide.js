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
 *   drag     the point is placed for them and the profile ringed; drag
 *            it along the profile (the rail's cursor). Next after a while.
 *   zoom     zoom in until the line draws in slope classes — "Show me"
 *            flies to the steepest stretch (pwaRouteOpener.showPoint).
 *   colours  what the colours mean; then "Keep exploring this example" or
 *            "Back to today" (SNOW-1244).
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
 * KEEP EXPLORING (SNOW-1244). Testing found that finishing took away the
 * route the visitor had just learned to read. The last step offers "Keep
 * exploring this example" beside "Back to today": the first ends the guide
 * but leaves the route, its profile and the example's day, with the pill
 * reading "Example route · <day>" and one button, Back to today, for as
 * long as they stay.
 *
 * LEAVING ALWAYS PUTS THE MAP BACK ON TODAY, AND SAYS SO. Exit (from the bar
 * or the pill) and Back to today all: drop the example from the session
 * (routes:share_dismiss — it stops being drawn and listed), close the rail,
 * commit today through 'snowdesk:scrub-to' as map_today.js does, and toast
 * "Back to today · <date>". Back to today then shows the finish card.
 *
 * Telemetry: map.example.opened, map.example.step_completed {step},
 * map.example.show_me, map.example.exited {step}, map.example.finished
 * {kept}, and map.example.left (Back to today after keeping exploring). A finished or exited walk
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

  /** @type {'waiting'|'running'|'exploring'|'ended'} */
  let phase = 'waiting';
  /** @type {string} */
  let step = exampleDate ? 'intro' : 'drag';
  let date = exampleDate || today;
  let railShowsExample = false;
  let unsubscribeCursor = null;
  let drag = null;
  let zoomStart = NaN;
  let stepTimer = null;
  let skipTimer = null;

  const emit = (name, props) => window.pwaTelemetry?.emit(name, props || {});

  const onExampleDay = () => !exampleDate || date === exampleDate;

  // ---- the words that are filled in ----

  const dayText = core.formatDay(exampleDate, locale);
  guide.querySelectorAll('[data-example-day]').forEach((el) => {
    el.textContent = dayText;
  });
  pill.querySelectorAll('[data-example-short-day]').forEach((el) => {
    el.textContent = core.formatShortDay(exampleDate, locale);
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
   * under the route panel (a selection's column); on a phone, and with no
   * route open, under the controls along the top edge. Never above those
   * controls. A phone's bar used to ride on top of the route's sheet;
   * Hugo moved it to the top (SNOW-1244) so the route and the leader line
   * from the profile to the dot show between the bar and the sheet.
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
    if (railEl && rail.isOpen() && !(rail.isSheet && rail.isSheet())) {
      top = railEl.getBoundingClientRect().bottom - mapTop + GAP;
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

  /**
   * Ring the profile while the drag step asks for it, and only then
   * (SNOW-1244: a tester tapped it and saw nothing happen).
   *
   * @param {boolean} on
   */
  const ringProfile = (on) => {
    const rail = window.pwaRouteRail && window.pwaRouteRail.element;
    const lane = rail && rail.querySelector('[data-route-rail-lane]');
    if (lane) lane.toggleAttribute('data-example-ring', on);
  };

  /** Bar, pill or neither; the badge on the date while it is the example's. */
  const paint = () => {
    const active = phase === 'running';
    const exploring = phase === 'exploring';
    const showBar = active && railShowsExample && onExampleDay();
    guide.hidden = !showBar;
    // Exploring, the pill stays put whatever the visitor opens or closes:
    // it is the one way back to today (SNOW-1244).
    pill.hidden = !(exploring || (active && !showBar));
    if (!pill.hidden) {
      const offDay = !exploring && !onExampleDay();
      pill.querySelector('[data-example-pill="paused"]').hidden = exploring || offDay;
      pill.querySelector('[data-example-pill="off-day"]').hidden = !offDay;
      pill.querySelector('[data-example-pill="exploring"]').hidden = !exploring;
      pill.querySelector('[data-example-resume]').hidden = exploring || offDay;
      if (backToDay) backToDay.hidden = !offDay;
      pill.querySelectorAll('[data-example-exit]').forEach((el) => {
        el.hidden = exploring;
      });
      const backToToday = pill.querySelector('[data-example-back-to-today]');
      if (backToToday) backToToday.hidden = !exploring;
    }
    if (badge) badge.hidden = !((active || exploring) && exampleDate && onExampleDay());
    if (showBar) paintStep();
    ringProfile(showBar && step === 'drag');
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
    if (step === 'drag') placePoint();
  };

  /**
   * Start the drag step with the point already on the route (SNOW-1244), so
   * the step asks for one gesture — a drag — rather than a tap to place the
   * point and then a drag. Leaves a point the visitor placed where it is.
   * map.js binds the route's cursor as the rail opens, so a first try that
   * finds nothing to place on is tried once more a moment later.
   *
   * @param {boolean} [retried]
   */
  function placePoint(retried = false) {
    const rail = window.pwaRouteRail;
    const cursor = rail && rail.cursor();
    if (!cursor || !railShowsExample || cursor.state().index !== null) return;
    const index = core.startIndex(rail.sampleCount ? rail.sampleCount() : 0);
    if (index === null) return;
    const placed = window.pwaRouteOpener?.showPoint?.(index);
    if (!placed && !retried) setTimeout(() => placePoint(true), 300);
  }

  /**
   * "Show me" (SNOW-1244): fly to the steepest stretch at the zoom where the
   * line shows the slope, with the point on it. Zooming by hand let the
   * route slide off the screen in testing; this keeps it in the middle.
   */
  const showMe = () => {
    if (phase !== 'running' || step !== 'zoom') return;
    const opener = window.pwaRouteOpener;
    const rail = window.pwaRouteRail;
    const cursor = rail && rail.cursor();
    let index = core.steepestIndex(opener?.slopeAngles ? opener.slopeAngles() : []);
    if (index === null) index = cursor ? cursor.state().index : null;
    if (index === null) index = core.startIndex(rail && rail.sampleCount ? rail.sampleCount() : 0);
    if (index === null) return;
    emit('map.example.show_me', {});
    // Half a level past the threshold, so the colours are not on the edge.
    // The map's padding keeps the bar clear, wherever it sits.
    opener?.showPoint?.(index, ZOOM_TARGET + 0.5);
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
    if (skipTimer !== null) clearTimeout(skipTimer);
    skipTimer = null;
    const skip = /** @type {HTMLElement|null} */ (guide.querySelector('[data-example-skip]'));
    if (skip) skip.hidden = true;
    if (step === 'drag') {
      if (drag) drag.reset();
      placePoint();
      // A drag that does not register still leaves a way on.
      skipTimer = setTimeout(() => {
        skipTimer = null;
        if (skip && phase === 'running' && step === 'drag') skip.hidden = false;
      }, core.NEXT_AFTER_MS);
    }
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
    if (skipTimer !== null) clearTimeout(skipTimer);
    skipTimer = null;
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

  /**
   * "Back to today" on the last step, or from the exploring pill: leave,
   * and offer the visitor's own track next.
   */
  const finish = () => {
    if (phase === 'running') {
      emit('map.example.step_completed', { step: step });
      emit('map.example.finished', { kept: false });
    } else if (phase === 'exploring') {
      emit('map.example.left', {});
    } else {
      return;
    }
    end();
    finishCard.hidden = false;
  };

  /**
   * "Keep exploring this example" (SNOW-1244): the guide is done, but the
   * route, its profile and the example's day stay until the visitor chooses
   * to leave. The pill keeps the way back to today.
   */
  const keepExploring = () => {
    if (phase !== 'running') return;
    emit('map.example.step_completed', { step: step });
    emit('map.example.finished', { kept: true });
    phase = 'exploring';
    if (stepTimer !== null) clearTimeout(stepTimer);
    stepTimer = null;
    if (unsubscribeCursor) unsubscribeCursor();
    unsubscribeCursor = null;
    try {
      localStorage.setItem(WALKED_KEY, 'yes');
    } catch (_) {
      // Private mode: as end() says.
    }
    paint();
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
    else if (target.closest('[data-example-skip]') && step === 'drag') completeStep();
    else if (target.closest('[data-example-show-me]')) showMe();
    else if (target.closest('[data-example-finish]')) finish();
    else if (target.closest('[data-example-keep]')) keepExploring();
    else if (target.closest('[data-example-back-to-today]')) finish();
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
    if (phase === 'exploring') return;
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
    if (phase === 'running' || phase === 'exploring') paint();
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
