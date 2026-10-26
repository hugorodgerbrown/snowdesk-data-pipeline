/*
 * static/js/map_resort_guide.js — the "Find a resort" walkthrough
 * (SNOW-1231).
 *
 * Started by 'snowdesk:resort-guide-requested': the welcome card's "Find a
 * resort" (map_tour_offer.js) and the example walkthrough's finish card
 * (map_example_guide.js). public/partials/_map_resort_guide.html renders
 * every word; resort_guide_core.js holds the steps and their rules:
 *
 *   search  search opens, led by the guide's words and a few popular
 *           resorts ('snowdesk:search-suggestions', which map.js lists
 *           above the visitor's own pins). Picking any resort finishes it.
 *   resort  the resort's sheet. The guide names the resort's region,
 *           says the danger is the region's, and rings the region row.
 *           Tapping it finishes the step.
 *   region  the region's sheet, which is the bulletin; Finish.
 *
 * It WATCHES rather than drives. Search, the place sheet (#place-sheet) and
 * the region sheet (#region-sheet) work exactly as they always do; a
 * MutationObserver on the three tells the guide what is open, and the
 * core decides what that means. NOTHING IS LOCKED, as in the example
 * walkthrough: close the sheet a step is about, or leave the guide's day,
 * and the bar folds into the paused pill, which keeps its step and offers
 * the way back (Resume, or "Back to <day>").
 *
 * OUT OF SEASON (#map-resort-guide's data-guide-date) it first moves the
 * map to that day — the example route's, which has a full bulletin — and
 * marks the date with the example walkthrough's "Example day" badge. Exit
 * and Finish put the map back on today and say so with that walkthrough's
 * toast (#map-example-toast). In season the map keeps its own day.
 *
 * Telemetry: map.resort_guide.opened, map.resort_guide.step_completed
 * {step}, map.resort_guide.exited {step}, map.resort_guide.finished, and
 * map.resort_guide.pinned from the finish card.
 *
 * Depends on resort_guide_core.js, example_guide_core.js (the day's
 * wording) and i18n_strings.js, loaded before it; map.js answers the
 * search and resort events, map_place_sheet.js and map_region_sheet.js
 * own the sheets, favourites.js saves the pin.
 */
(function mapResortGuideInit() {
  'use strict';

  const root = document.getElementById('map-resort-guide');
  const bar = document.getElementById('map-resort-guide-bar');
  const pill = document.getElementById('map-resort-guide-pill');
  const finishCard = document.getElementById('map-resort-guide-finish');
  const mapEl = document.getElementById('map');
  const core = window.pwaResortGuideCore;
  const dayCore = window.pwaExampleGuideCore;
  if (!root || !bar || !pill || !finishCard || !mapEl || !core || !dayCore) return;

  const STRINGS = self.pwaStrings.read('map-resort-guide-strings-template', {
    'step-of': 'Find a resort · %(n)s of %(count)s',
    popular: 'Popular resorts',
    'finish-title': 'Keep %(name)s one tap away',
    pin: 'Pin %(name)s',
    pinned: '%(name)s is pinned',
    'sign-in-to-pin': 'Sign in to pin %(name)s',
    'back-to-day': 'Back to %(date)s',
    'back-to-today': 'Back to today · %(date)s',
  });
  const locale = document.documentElement.lang || undefined;
  const guideDate = root.dataset.guideDate || '';
  const mapUrl = root.dataset.mapUrl || '/map/';
  const scrubber = document.getElementById('season-scrubber');
  const today = (scrubber && scrubber.dataset.today) || '';
  const badge = document.getElementById('map-example-day-badge');
  const toast = document.getElementById('map-example-toast');
  const lead = root.querySelector('[data-resort-guide-lead]');
  const searchPill = document.getElementById('search-pill');
  const searchResults = document.getElementById('search-results');
  const placeSheet = document.getElementById('place-sheet');
  const regionSheet = document.getElementById('region-sheet');
  const dots = Array.from(bar.querySelectorAll('.map-example-guide-dots li'));
  const backToDay = pill.querySelector('[data-resort-guide-back-to-day]');
  const pinBtn = finishCard.querySelector('[data-resort-guide-pin]');
  const signInLink = finishCard.querySelector('[data-resort-guide-signin]');
  const finishTitle = document.getElementById('map-resort-guide-finish-title');
  const DESKTOP_QUERY = '(min-width: 640px)';
  // A beat between a step done and the next, so the sheet that answered
  // it is seen to open before the words change.
  const STEP_PAUSE_MS = 500;
  // Picking a resort collapses search a moment before its sheet opens;
  // the pill waits this long before reading that as the visitor leaving.
  const SETTLE_MS = 600;
  const GAP = 12;
  // Long enough for a phone sheet to settle at a new detent.
  const REVEAL_DELAY_MS = 400;

  let popular = [];
  try {
    const data = document.getElementById('map-resort-guide-popular');
    popular = data ? JSON.parse(data.textContent || '[]') : [];
  } catch (_) {
    popular = [];
  }

  /** @type {'waiting'|'running'|'ended'} */
  let phase = 'waiting';
  /** @type {string} */
  let step = 'search';
  let date = (scrubber && scrubber.dataset.date) || today;
  /** The resort the visitor picked: what the sheet's star says about it. */
  let resort = null;
  let stepTimer = null;
  let settleTimer = null;
  let shown = 'none';

  const emit = (name, props) => window.pwaTelemetry?.emit(name, props || {});
  const isPhone = () => typeof window.matchMedia !== 'function'
    || !window.matchMedia(DESKTOP_QUERY).matches;
  const isOpen = (el) => Boolean(el && !el.hasAttribute('hidden'));
  const onGuideDay = () => !guideDate || date === guideDate;
  const fill = (selector, text) => {
    document.querySelectorAll(selector).forEach((el) => {
      el.textContent = text;
    });
  };

  // ---- the words that are filled in ----

  fill('[data-resort-guide-day]', dayCore.formatDay(guideDate, locale));
  if (backToDay) {
    backToDay.textContent = self.pwaStrings.interpolate(STRINGS['back-to-day'], {
      date: dayCore.formatShortDay(guideDate, locale),
    });
  }

  /** The resort's region row on the open place sheet, if it has one. */
  const regionRow = () => (isOpen(placeSheet)
    ? placeSheet.querySelector('[data-testid="resort-region-link"]')
    : null);

  /**
   * Read the picked resort off its sheet: its name and region from the
   * region row and the heading, its slug and pin from the Save star.
   */
  const readResort = () => {
    const row = regionRow();
    if (!row) return;
    // The star is there only for a signed-in visitor; the body's panel key
    // ("resort:<slug>") is there for everyone.
    const star = placeSheet.querySelector('[data-resort-star]');
    const keyed = placeSheet.querySelector('[data-panel-key^="resort:"]');
    const key = keyed ? keyed.getAttribute('data-panel-key') || '' : '';
    const heading = placeSheet.querySelector('h2, h1');
    const regionName = row.querySelector('.font-semibold');
    resort = {
      slug: (star && star.getAttribute('data-resort-slug')) || key.slice('resort:'.length),
      name: (star && star.getAttribute('data-resort-name'))
        || (keyed && keyed.getAttribute('data-panel-label'))
        || (heading ? heading.textContent.trim() : ''),
      lat: (star && star.getAttribute('data-resort-lat')) || '',
      lon: (star && star.getAttribute('data-resort-lon')) || '',
      favourited: Boolean(star && star.getAttribute('data-favourited') === 'true'),
      regionId: row.getAttribute('data-panel-id') || '',
      regionName: regionName ? regionName.textContent.trim() : '',
    };
    fill('[data-resort-guide-name]', resort.name);
    fill('[data-resort-guide-region]', resort.regionName);
  };

  // ---- what the map shows ----

  /** @returns {{searchOpen: boolean, resortOpen: boolean, regionOpen: boolean, onGuideDay: boolean}} */
  const mapState = () => ({
    searchOpen: Boolean(
      (searchPill && searchPill.getAttribute('data-state') === 'expanded')
      || (searchResults && !searchResults.hidden),
    ),
    resortOpen: Boolean(regionRow()),
    regionOpen: isOpen(regionSheet),
    onGuideDay: onGuideDay(),
  });

  // ---- placing the bar and the pill ----

  /** The sheet the current step talks about, while it is open. */
  const stepSheet = () => {
    if (step === 'resort' && isOpen(placeSheet)) return placeSheet;
    if (step === 'region' && isOpen(regionSheet)) return regionSheet;
    return null;
  };

  /** @param {HTMLElement} el */
  const place = (el) => {
    const mapBox = mapEl.getBoundingClientRect();
    const cluster = document.getElementById('map-utility-cluster');
    const floor = (cluster ? cluster.getBoundingClientRect().bottom : mapBox.top) - mapBox.top + GAP;
    const sheet = el === bar ? stepSheet() : null;
    const pos = core.barPosition({
      phone: isPhone(),
      sheet: sheet ? sheet.getBoundingClientRect() : null,
      map: mapBox,
      floor: floor,
      height: el.offsetHeight,
      gap: GAP,
      overSheet: step === 'resort' ? 'top' : 'bottom',
    });
    el.style.top = pos.top === null ? 'auto' : `${pos.top}px`;
    el.style.bottom = pos.bottom === null ? '' : `${pos.bottom}px`;
    el.style.left = pos.left === null ? '' : `${pos.left}px`;
  };

  // A phone's sheet moves between detents with no event of its own, so the
  // bar follows it frame by frame while it shows (map_example_guide.js).
  let frame = 0;
  const follow = () => {
    cancelAnimationFrame(frame);
    if (bar.hidden && pill.hidden) return;
    if (!bar.hidden) place(bar);
    if (!pill.hidden) place(pill);
    frame = requestAnimationFrame(follow);
  };

  // ---- painting ----

  /** Ring the row step two asks for, and only then. */
  const ring = () => {
    document.querySelectorAll('[data-resort-guide-ring]').forEach((el) => {
      el.removeAttribute('data-resort-guide-ring');
    });
    const row = phase === 'running' && step === 'resort' ? regionRow() : null;
    if (row) row.setAttribute('data-resort-guide-ring', '');
  };

  const paintStep = () => {
    const n = core.stepNumber(step);
    fill('[data-resort-guide-eyebrow]', self.pwaStrings.interpolate(STRINGS['step-of'], {
      n: String(n),
      count: String(core.STEPS.length),
    }));
    bar.querySelectorAll('[data-resort-guide-step]').forEach((el) => {
      el.hidden = el.getAttribute('data-resort-guide-step') !== step;
    });
    dots.forEach((dot, i) => dot.classList.toggle('is-on', i < n));
  };

  /**
   * Draw what the core says the map's state calls for.
   *
   * @param {boolean} [settled] The beat below has passed: draw the pill.
   */
  const paint = (settled = false) => {
    const view = core.surface(phase, step, mapState());
    // A pill that would replace the bar waits a beat: a pick or a tap on
    // the region row closes one surface just before the next one opens.
    if (!settled && view.show === 'pill' && shown !== 'pill' && shown !== 'none' && settleTimer === null) {
      settleTimer = setTimeout(() => {
        settleTimer = null;
        paint(true);
      }, SETTLE_MS);
      return;
    }
    if (view.show !== 'pill' && settleTimer !== null) {
      clearTimeout(settleTimer);
      settleTimer = null;
    }
    if (settleTimer !== null) return;
    shown = view.show;
    paintStep();
    bar.hidden = view.show !== 'bar';
    pill.hidden = view.show !== 'pill';
    if (view.show === 'pill') {
      const offDay = view.pill === 'off-day';
      pill.querySelector('[data-resort-guide-pill="paused"]').hidden = offDay;
      pill.querySelector('[data-resort-guide-pill="off-day"]').hidden = !offDay;
      pill.querySelector('[data-resort-guide-resume]').hidden = offDay;
      if (backToDay) backToDay.hidden = !offDay;
    }
    if (badge) badge.hidden = !(phase === 'running' && guideDate && onGuideDay());
    ring();
    follow();
  };

  // ---- the steps ----

  /** Offer the popular resorts in search, led by the guide's words. */
  const suggest = (on) => {
    document.dispatchEvent(new CustomEvent('snowdesk:search-suggestions', {
      detail: on ? { heading: STRINGS.popular, resorts: popular, lead: lead } : { resorts: [] },
    }));
  };

  const openSearch = () => {
    const toggle = document.getElementById('search-toggle');
    if (toggle && searchPill && searchPill.getAttribute('data-state') !== 'expanded') toggle.click();
    // Focus draws the empty field's list, popular resorts first.
    const input = document.getElementById('search-input');
    if (input instanceof HTMLInputElement) input.focus();
  };

  /**
   * The region row sits low in the resort's sheet, under its weather and
   * facts: on a phone, under the peek it opens at. Raise a phone sheet to
   * full, and on every width scroll the row into the lower part of the
   * sheet, clear of the bar laid over its top.
   */
  const revealRegionRow = () => {
    if (!placeSheet) return;
    if (isPhone()) window.pwaPlaceSheet?.detent?.('full');
    // Once the sheet has risen.
    setTimeout(() => {
      const row = regionRow();
      if (!row) return;
      const offset = row.getBoundingClientRect().bottom - placeSheet.getBoundingClientRect().top;
      placeSheet.scrollTop += offset - placeSheet.clientHeight * 0.8;
    }, REVEAL_DELAY_MS);
  };

  /** @param {string} next */
  const enterStep = (next) => {
    step = next;
    if (step !== 'search') suggest(false);
    if (step === 'resort') revealRegionRow();
    emit('map.resort_guide.step_completed', { step: core.STEPS[core.stepNumber(step) - 2] });
    paint();
  };

  /** Move on when the map shows the step done, after a beat. */
  const check = () => {
    if (phase !== 'running') return;
    if (step === 'search' || step === 'resort') readResort();
    if (stepTimer === null && core.isStepDone(step, mapState())) {
      const done = step;
      stepTimer = setTimeout(() => {
        stepTimer = null;
        if (phase !== 'running' || step !== done) return;
        const next = core.nextStep(done);
        if (next) enterStep(next);
      }, STEP_PAUSE_MS);
    }
    paint();
  };

  const start = () => {
    finishCard.hidden = true;
    if (phase === 'running') return;
    phase = 'running';
    step = 'search';
    resort = null;
    emit('map.resort_guide.opened', { offseason: Boolean(guideDate) });
    if (guideDate && date !== guideDate) goToGuideDay();
    suggest(true);
    openSearch();
    paint();
  };

  // ---- leaving ----

  /** Stop, and put the map back on today if the guide moved it. */
  const end = () => {
    phase = 'ended';
    if (stepTimer !== null) clearTimeout(stepTimer);
    if (settleTimer !== null) clearTimeout(settleTimer);
    stepTimer = null;
    settleTimer = null;
    suggest(false);
    paint();
    if (!guideDate || !today) return;
    if (date !== today) {
      document.dispatchEvent(new CustomEvent('snowdesk:scrub-to', {
        detail: { date: today, source: 'resort-guide' },
      }));
    }
    if (toast) {
      const body = toast.querySelector('[data-toast-body]');
      if (body) {
        body.textContent = self.pwaStrings.interpolate(STRINGS['back-to-today'], {
          date: dayCore.formatShortDay(today, locale),
        });
      }
      toast.classList.remove('hidden');
    }
  };

  const exit = () => {
    if (phase !== 'running') return;
    emit('map.resort_guide.exited', { step: step });
    end();
    // The guide's words leave the dropdown with it.
    if (step === 'search' && searchResults && !searchResults.hidden) {
      document.getElementById('search-input')?.focus();
    }
  };

  /** The finish card's pin: favourites.js saves it, or sign-in first. */
  const paintFinish = () => {
    const name = resort ? resort.name : '';
    if (finishTitle) {
      finishTitle.textContent = self.pwaStrings.interpolate(
        STRINGS[resort && resort.favourited ? 'pinned' : 'finish-title'],
        { name: name },
      );
    }
    if (pinBtn) {
      pinBtn.hidden = !resort || !resort.slug || resort.favourited;
      pinBtn.disabled = false;
      pinBtn.textContent = self.pwaStrings.interpolate(STRINGS.pin, { name: name });
      if (resort) {
        pinBtn.setAttribute('data-resort-slug', resort.slug);
        pinBtn.setAttribute('data-resort-name', resort.name);
        pinBtn.setAttribute('data-resort-lat', resort.lat);
        pinBtn.setAttribute('data-resort-lon', resort.lon);
        pinBtn.setAttribute('data-favourited', 'false');
      }
    }
    if (signInLink) {
      signInLink.hidden = !resort || !resort.name;
      signInLink.textContent = self.pwaStrings.interpolate(STRINGS['sign-in-to-pin'], { name: name });
      const base = (signInLink.getAttribute('href') || '').split('?')[0];
      signInLink.setAttribute('href', core.signInHref(base, mapUrl, resort ? resort.slug : ''));
    }
  };

  const finish = () => {
    if (phase !== 'running') return;
    emit('map.resort_guide.step_completed', { step: step });
    emit('map.resort_guide.finished', {});
    // The bulletin has been read; the finish card takes its place, as it
    // would sit under it otherwise.
    regionSheet?.querySelector('[data-action="dismiss"]')?.click();
    end();
    paintFinish();
    finishCard.hidden = false;
  };

  // ---- wiring ----

  /** Commit the guide's day, as the calendar would. */
  function goToGuideDay() {
    if (!guideDate) return;
    document.dispatchEvent(new CustomEvent('snowdesk:scrub-to', {
      detail: { date: guideDate, source: 'resort-guide' },
    }));
  }

  /** Reopen what the paused step was about. */
  const resume = () => {
    if (phase !== 'running') return;
    if (step === 'search') {
      suggest(true);
      openSearch();
    } else if (step === 'resort' && resort && resort.slug) {
      document.dispatchEvent(new CustomEvent('snowdesk:resort-open', { detail: { slug: resort.slug } }));
    } else if (step === 'region' && resort && resort.regionId) {
      document.dispatchEvent(new CustomEvent('snowdesk:region-open', { detail: { region_id: resort.regionId } }));
    }
  };

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest('[data-resort-guide-exit]')) exit();
    else if (target.closest('[data-resort-guide-finish]')) finish();
    else if (target.closest('[data-resort-guide-resume]')) resume();
    else if (target.closest('[data-resort-guide-back-to-day]')) goToGuideDay();
    else if (target.closest('[data-resort-guide-done]')) finishCard.hidden = true;
    else if (pinBtn && target.closest('[data-resort-guide-pin]')) {
      // favourites.js saves nothing on a device that cannot queue the
      // create (it says so), so the card claims nothing either.
      if (!window.MapSheet?.canQueueMutations?.()) return;
      emit('map.resort_guide.pinned', {});
      // After favourites.js's own document listener has read the button.
      setTimeout(() => {
        pinBtn.disabled = true;
        pinBtn.textContent = self.pwaStrings.interpolate(STRINGS.pinned, {
          name: resort ? resort.name : '',
        });
      }, 0);
    }
  });

  document.addEventListener('snowdesk:resort-guide-requested', start);

  document.addEventListener('snowdesk:date-changed', (event) => {
    const detail = /** @type {CustomEvent} */ (event).detail;
    if (!detail || !detail.date) return;
    date = detail.date;
    if (phase === 'running') paint();
  });

  const observer = new MutationObserver(check);
  [searchPill, searchResults, placeSheet, regionSheet].forEach((el) => {
    if (!el) return;
    observer.observe(el, {
      attributes: true,
      attributeFilter: ['hidden', 'data-state'],
      childList: el === placeSheet,
      subtree: el === placeSheet,
    });
  });

  window.addEventListener('resize', follow);
}());
