/*
 * static/js/map_timelapse.js — the five season-playback transport buttons.
 *
 * SNOW-610, step 4: extracted verbatim from map.js. Runs at parse time and
 * binds its own DOM, so it loads AFTER map.js in the position it held
 * inside that file — document order is execution order for deferred
 * classic scripts. Shared state (`MAP`, `FEATURE_BY_REGION_ID`,
 * `COUNTRY_STATE`, …) comes from static/js/map_state.js; shared helpers
 * (`getSeasonRatings`, `repaintRegionsForDate`, the localStorage trio)
 * from static/js/map_shared.js. Both load first.
 */

// Season timelapse — five transport buttons on the scrubber:
//   |<  skip to season start
//   <   play in reverse from current thumb position (second press = stop)
//   >   play forward from current thumb position (second press = stop)
//   >|  skip to season end
//
// Each frame repaints region colours via feature-state and announces a
// snowdesk:date-changed event so the date readout stays in sync. Pressing
// the opposite play button mid-playback flips direction from the current
// frame without resetting the frame index.
//
// SNOW-660: playback chooses a day like any other surface, so the URL has
// to say which — ``?d=`` is written at the points playback SETTLES (a stop,
// and the two skip jumps), never per frame. See ``writeUrlDateParam`` in
// map_shared.js for why the rate differs from the scrubber's.
(function timelapseInit() {
  const playButton = document.getElementById('scrubber-play');
  if (!playButton) return;

  // BASE_FRAME_MS gives ~5 fps — consistent in both forward and reverse.
  const BASE_FRAME_MS = 200;

  // Drive the scrubber thumb so playback position is visible.
  const scrubber = document.getElementById('season-scrubber');
  const scrubberThumb = scrubber ? scrubber.querySelector('.season-scrubber-thumb') : null;
  // SNOW-794: the scrubber's TRACK span (twelve months ending today), not the
  // avalanche season — the two used to be one pair under one name. Playback
  // still steps through ``sortedDates``, real data days; this is only how a
  // frame becomes a thumb position.
  const seasonStartMs = scrubber ? Date.parse(scrubber.dataset.seasonStart) : NaN;
  const seasonEndMs = scrubber ? Date.parse(scrubber.dataset.seasonEnd) : NaN;
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  const reverseButton = document.getElementById('scrubber-reverse');
  const skipStartButton = document.getElementById('scrubber-skip-start');
  const skipEndButton = document.getElementById('scrubber-skip-end');

  // Active playback direction: 1 = forward, -1 = reverse.
  let direction = 1;

  const moveScrubber = (dateKey) => {
    if (!scrubberThumb || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) return;
    const dateMs = Date.parse(dateKey);
    if (Number.isNaN(dateMs)) return;
    const pct = Math.max(0, Math.min(100, ((dateMs - seasonStartMs) / seasonSpanMs) * 100));
    scrubberThumb.style.left = pct + '%';
    // Keep aria-valuenow in lock-step with the visual thumb position so
    // ``currentFrameIdx`` resumes from the right spot when the user stops
    // playback mid-season and then presses play again — without this,
    // the next start() falls back to the last user-committed pct rather
    // than the last frame painted by the timelapse.
    if (scrubber) {
      scrubber.setAttribute('aria-valuenow', String(Math.round(pct)));
    }
  };

  // Determine the frame index to start from so playback begins at the
  // current thumb position rather than always rewinding to frame 0.
  const currentFrameIdx = () => {
    const ariaNow = scrubber ? parseFloat(scrubber.getAttribute('aria-valuenow')) : NaN;
    if (window.pwaScrubberCore) {
      return window.pwaScrubberCore.nearestFrameIndex(sortedDates, ariaNow, seasonStartMs, seasonSpanMs);
    }
    if (!sortedDates || sortedDates.length === 0) return 0;
    if (!Number.isFinite(ariaNow) || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) {
      return 0;
    }
    const targetMs = seasonStartMs + (ariaNow / 100) * seasonSpanMs;
    // Find the nearest sortedDates entry to the current thumb position.
    let best = 0;
    let bestDelta = Math.abs(Date.parse(sortedDates[0]) - targetMs);
    for (let i = 1; i < sortedDates.length; i++) {
      const delta = Math.abs(Date.parse(sortedDates[i]) - targetMs);
      if (delta < bestDelta) { best = i; bestDelta = delta; }
    }
    return best;
  };

  let cache = null;        // {date_iso: {region_id: int}}
  let sortedDates = null;  // ascending list of date keys
  let frameIdx = 0;
  let timer = null;

  // SNOW-660: the last frame actually painted — the day currently on screen.
  //
  // ``stop()`` cannot ask ``frameIdx`` for it: the boundary stop runs with
  // the index already stepped out of range (``nextFrame`` returns the
  // incremented value alongside ``done``), so ``sortedDates[frameIdx]``
  // there is undefined. This is set where the paint happens, which is the
  // only place that knows what the map is showing.
  let lastPaintedDate = null;

  const announce = (dateKey) => {
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: dateKey, source: 'timelapse' },
    }));
  };

  const applyFrame = (dateKey) => {
    repaintRegionsForDate(dateKey, cache);
    moveScrubber(dateKey);
    lastPaintedDate = dateKey;
    announce(dateKey);
  };

  // Hoisted so start() can re-arm setInterval at a new direction without
  // losing the current frame index.
  const tick = () => {
    if (window.pwaScrubberCore) {
      const result = window.pwaScrubberCore.nextFrame(frameIdx, direction, sortedDates.length);
      frameIdx = result.frameIdx;
      if (result.done) {
        // Boundary reached (forward end or reverse start): last valid frame
        // already painted — stop so the value settles. SNOW-660: playback
        // ran to its own end, so the frame it settled on is the day the
        // visitor is left looking at, and the URL says so.
        stop({ syncUrl: true });
        return;
      }
      applyFrame(sortedDates[frameIdx]);
      return;
    }
    frameIdx += direction;
    if (direction === 1 && frameIdx >= sortedDates.length) {
      // Forward end: last frame already painted — stop so the value settles.
      stop({ syncUrl: true });
      return;
    }
    if (direction === -1 && frameIdx < 0) {
      // Reverse start: first frame already painted — stop.
      stop({ syncUrl: true });
      return;
    }
    applyFrame(sortedDates[frameIdx]);
  };

  /**
   * Halt playback, leaving the map on the frame it reached.
   *
   * SNOW-660: `opts.syncUrl` writes `?d=` for that frame — playback chooses
   * a day just as the scrubber does, so the URL has to say which, or a
   * reload comes back to a map with no day chosen at all. It is written HERE
   * and not per frame because `history.replaceState` is throttled by Safari
   * (~100 calls per 30s, and it THROWS on the 101st), and five frames a
   * second would hit that inside half a minute of playback — see
   * `writeUrlDateParam` in map_shared.js.
   *
   * Off by default because two of this function's four callers are not the
   * user ending playback: the `snowdesk:date-changed` listener stops in
   * deference to a scrub that has ALREADY written its own, later date, and
   * the basemap-swap listener stops for a reason that is not about dates at
   * all. Writing in either case would replace the current URL with a stale
   * frame's.
   *
   * @param {{syncUrl?: boolean}} [opts]
   */
  const stop = (opts = {}) => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
    if (opts.syncUrl && lastPaintedDate) writeUrlDateParam(lastPaintedDate);
    // Reset data-state on both play transport buttons.
    playButton.dataset.state = 'stopped';
    playButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play']);
    if (reverseButton) {
      reverseButton.dataset.state = 'stopped';
      reverseButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play-reverse']);
    }
    // Leave the map painted on the current frame — do not clear
    // feature-state or reset the thumb. The user sees what was playing.
    IS_PLAYING = false;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing: false } }));
  };

  // start(directionArg) — begins playback from the current thumb position.
  // If timer is already running (direction flip mid-playback), re-arms the
  // interval at the new direction without resetting frameIdx so position is
  // preserved.
  const start = async (directionArg) => {
    if (!MAP || !MAP.isStyleLoaded()) return;
    if (cache === null) {
      try {
        cache = await getSeasonRatings();
        sortedDates = Object.keys(cache).sort();
      } catch (_err) {
        return;
      }
    }
    if (sortedDates.length === 0) return;

    direction = directionArg;

    // Only update frameIdx when starting fresh (not a direction flip).
    if (timer === null) {
      frameIdx = currentFrameIdx();
    }

    // Clear any existing timer before arming the new one.
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }

    // Update button states to reflect which transport is now active.
    if (directionArg === 1) {
      playButton.dataset.state = 'playing';
      playButton.setAttribute('aria-label', MAP_STRINGS['timelapse-stop']);
      if (reverseButton) {
        reverseButton.dataset.state = 'stopped';
        reverseButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play-reverse']);
      }
    } else {
      if (reverseButton) {
        reverseButton.dataset.state = 'playing';
        reverseButton.setAttribute('aria-label', MAP_STRINGS['timelapse-stop-reverse']);
      }
      playButton.dataset.state = 'stopped';
      playButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play']);
    }

    IS_PLAYING = true;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing: true } }));
    applyFrame(sortedDates[frameIdx]);
    timer = setInterval(tick, BASE_FRAME_MS);
  };

  // When the scrubber commits a new date, the timelapse must surrender
  // control — both paint via feature-state on the same source, so a
  // running timer would fight a user scrub.
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (timer !== null && (!e.detail || e.detail.source !== 'timelapse')) {
      stop();
    }
  });

  // SNOW-58: a basemap swap wipes the regions source mid-frame — stop
  // so setInterval doesn't paint into a half-loaded style.
  document.addEventListener('snowdesk:basemap-changing', () => {
    if (timer !== null) stop();
  });

  // Play-forward button: running forward → stop; else → start(1).
  // Pressing while reverse is playing flips direction from the current frame.
  playButton.addEventListener('click', () => {
    if (timer !== null && direction === 1) {
      // SNOW-660: the user ending playback IS a choice of day — the frame
      // they stopped on is what they want to look at, so the URL says so.
      stop({ syncUrl: true });
    } else {
      start(1);
    }
  });

  // Play-reverse button: running reverse → stop; else → start(-1).
  // Pressing while forward is playing flips direction from the current frame.
  if (reverseButton) {
    reverseButton.addEventListener('click', () => {
      if (timer !== null && direction === -1) {
        stop({ syncUrl: true });
      } else {
        start(-1);
      }
    });
  }

  // Skip-to-start / skip-to-end: jump the thumb to the first/last data
   // day. Falls back to ``data-season-start``/``data-season-end`` before
  // the ratings cache resolves. moveScrubber owns the thumb position +
  // aria-valuenow update; the synthetic date-changed event (source:
  // 'scrubber') causes the running timelapse to surrender control via
  // its own listener.
  //
  // SNOW-660: one discrete user action, one settled day — so it writes
  // ``?d=`` exactly as a scrubber commit does. It announces itself as
  // ``source: 'scrubber'`` and the listener above therefore stops playback
  // WITHOUT its own URL write, which is right: this write is the later one
  // and names the day the jump landed on, not the frame it interrupted.
  const commitJump = (target) => {
    if (!target) return;
    if (cache) repaintRegionsForDate(target, cache);
    moveScrubber(target);
    lastPaintedDate = target;
    writeUrlDateParam(target);
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: target, source: 'scrubber' },
    }));
  };

  if (skipStartButton) {
    skipStartButton.addEventListener('click', () => {
      commitJump(sortedDates ? sortedDates[0] : (scrubber ? scrubber.dataset.seasonStart : null));
    });
  }

  if (skipEndButton) {
    skipEndButton.addEventListener('click', () => {
      commitJump(sortedDates ? sortedDates[sortedDates.length - 1] : (scrubber ? scrubber.dataset.seasonEnd : null));
    });
  }
})();
