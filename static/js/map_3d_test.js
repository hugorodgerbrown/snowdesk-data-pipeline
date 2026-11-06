// @ts-check
/*
 * static/js/map_3d_test.js — "Test 3D on this device" on /map/ (SNOW-1456).
 *
 * Loaded after map_view_3d.js, for a reader in the map_3d and map_modes
 * flags (public/map.html). It runs the frame-time probe that /map/3d/
 * carried (SNOW-1448) on the reader's own map, as it is — their basemap's
 * overlays and switches — and tells them, in one line, how well this
 * device draws 3D (docs/decisions/a-3d-map-is-maplibre-terrain.md).
 *
 * ASKED FOR by snowdesk:map-3d-test-requested: the "?" menu's "Test 3D on
 * this device" (map_help_menu.js), the "Test this device" link on the
 * why-slow line (map_view_3d.js) and Test again. `/map/?probe=1` starts it
 * on load with Details open, and for that run only turns on Steep terrain
 * and SAC tours, the layers the SNOW-1448 budget was measured with,
 * turning them off again after. Conditions holds both off the map, so a
 * probe run in Conditions switches to Terrain and back again after.
 *
 * THE RUN. window.pwaMapView.canTest() says whether it may run at all
 * (offline, a device the gate keeps out, a lost WebGL context, a route
 * being drawn each get their own line, and nothing runs). beginTest()
 * saves the camera and the view and enters 3D, even when the watchdog has
 * laid it flat for the visit, with the watchdog resting. Then, as the
 * probe did: wait for the map to go idle, and for each of
 * map_3d_core.PROBE_PASSES jump to it over Saas-Fee, wait for its tiles
 * (nextIdle, capped) and turn the camera through a quarter circle at a
 * steady rate (timeTurn, `essential` so reduced motion does not skip it),
 * recording the interval between animation frames. map_3d_core.testVerdict
 * judges the passes by the watchdog's own rule — smooth, stutter, slow or
 * unmeasured — and endTest({verdict}) puts the camera and the view back; a
 * smooth or stutter verdict lifts the watchdog's latch.
 *
 * ENDED EARLY by the panel's × ("Stop the test"), Escape, a hidden tab, a
 * lost WebGL context or the map leaving 3D (snowdesk:map-view-changed to
 * flat: the 2D switch, offline, out of Switzerland), which would time the
 * rest flat: the turn stops, endTest({}) restores the camera and the view,
 * and there is no verdict and nothing to send. A throw mid-run ends it the
 * same way and shows "could not time". The pixel ratio and canvas size are
 * read before endTest, while the 3D cap still holds.
 *
 * THE RESULT. The panel (public/partials/_map_3d_test_panel.html, a form
 * panel on includes/_overlay_sheet.html) shows the verdict, each pass as
 * keeps up or falls behind, and Details: frameStats' figures per pass, the
 * GPU and the screen. "Send anonymously" is opt-in: it POSTs the result to
 * /api/map-3d-test/ with the page's CSRF token; a 204 is "Sent to
 * Snowdesk, anonymously.", anything else "Could not send the result", with
 * Send left to try again. An unmeasured run says so, offers Test again and
 * cannot be sent. The "…" menu holds Share (the full JSON on a probe run,
 * as /map/3d/'s probe shared it; a few readable lines otherwise) and Test
 * again; Test again starts after its click is done, so MapSheet's
 * click-outside never sees the replaced button as a tap outside. The
 * result records the layers-menu switches that were on and drawn (not
 * held off by the mode, pwaMapModeState), from the keys the panel lists
 * (apps/map/models.py MAP_3D_TEST_OVERLAYS).
 *
 * Every word is in the panel's markup or its strings <template>
 * (#map-3d-test-strings-template), read through window.pwaStrings.
 */
(function map3dTestInit() {
  'use strict';

  const sheetEl = document.getElementById('map-3d-test-sheet');
  const template = document.getElementById('map-3d-test-template');
  const core = self.pwaMap3dCore;
  const mapState = window.snowdeskMapState;
  const map = mapState ? mapState.map : null;
  const win = /** @type {any} */ (window);
  if (!sheetEl || !(template instanceof HTMLTemplateElement) || !core || !map || !win.MapSheet) return;
  const body = template.content.querySelector('[data-map-3d-test]');
  if (!(body instanceof HTMLElement)) return;

  /** Where Send posts. */
  const SEND_URL = body.dataset.url || '';
  /** The switches a result may record as on, in the server's order. */
  const OVERLAY_KEYS = (body.dataset.overlayKeys || '').split(' ').filter(Boolean);
  /** How long to wait for the map to go idle at the start, and per pass. */
  const FIRST_IDLE_CAP_MS = 60000;
  const SETTLE_CAP_MS = 30000;

  const STRINGS = self.pwaStrings.read('map-3d-test-strings-template', {
    'seconds-left': '%(s)s s left',
    'figure-median': 'median %(ms)s ms',
    'figure-fps': '%(fps)s fps',
    'figure-p95': 'p95 %(ms)s ms',
    'figure-over': '%(pct)s over %(budget)s ms',
    'figure-windows': '%(slow)s of %(windows)s windows slow',
    'figure-settle': 'tiles in %(s)s s',
    'figure-gpu': 'GPU · %(gpu)s',
    'gpu-unknown': 'not given',
    'figure-screen': 'screen · %(width)s × %(height)s at %(dpr)s×, drawn at %(ratio)s×',
    'figure-rule': 'rule · median ≥ %(budget)s ms in %(n)s windows of %(s)s s in a row',
    'keeps-up': 'keeps up',
    'falls-behind': 'falls behind',
    'pass-0': 'Close up, over Saas-Fee',
    'pass-1': 'Zoomed out, over Valais',
    'verdict-smooth': '3D runs smoothly on this phone',
    'verdict-stutter': '3D may stutter when zoomed out',
    'verdict-slow': '3D is too slow on this phone',
    'share-line': '%(label)s: %(status)s, median %(median)s ms',
    'share-title': 'Snowdesk 3D test',
    copied: 'Result copied',
    'copy-failed': 'Could not copy the result',
  });
  const fill = self.pwaStrings.interpolate;

  /**
   * @typedef {Object} Run
   * @property {boolean} cancelled
   * @property {Array<() => void>} stops What ends each wait the run is in.
   * @property {() => void} restoreLayers Turns off what a probe run turned on.
   */

  /** @type {?Run} The run now going, if one is. */
  let run = null;
  /** @type {?Record<string, any>} The result on show, what Send posts. */
  let result = null;
  /** @type {?Record<string, any>} The full report a probe run's Share sends. */
  let report = null;
  /** Whether the result on show came from `?probe=1`. */
  let probeRun = false;
  let sending = false;

  const sheet = win.MapSheet.attach(sheetEl, {
    // What opens it is not a click outside it.
    triggerSelector: '[data-map-3d-test-open], [data-help-menu="test-3d"]',
    onTeardown: () => {
      cancel();
      result = null;
      report = null;
    },
  });

  // ---- The panel ----------------------------------------------------------

  /** @returns {?HTMLElement} The open panel's body. */
  const root = () => /** @type {?HTMLElement} */ (sheetEl.querySelector('[data-map-3d-test]'));

  /**
   * @param {string} selector
   * @returns {?HTMLElement}
   */
  const find = (selector) => {
    const el = root();
    return el ? /** @type {?HTMLElement} */ (el.querySelector(selector)) : null;
  };

  /** Show the panel with a fresh body: nothing of the last result is left. */
  const openPanel = () => {
    sheetEl.replaceChildren(template.content.cloneNode(true));
    if (!sheet.isOpen()) sheet.open();
  };

  /**
   * Show one of the body's states.
   *
   * @param {string} name
   */
  const show = (name) => {
    const el = root();
    if (!el) return;
    el.dataset.testState = name;
    for (const part of el.querySelectorAll('[data-test-show]')) {
      /** @type {HTMLElement} */ (part).hidden = !(/** @type {HTMLElement} */ (part).dataset.testShow || '').split(' ').includes(name);
    }
  };

  /**
   * Mark the running step and the share of the test done.
   *
   * @param {string} current `load`, or a pass's index.
   * @param {number} fraction 0 to 1.
   */
  const progress = (current, fraction) => {
    const el = root();
    if (!el) return;
    const order = ['load', ...core.PROBE_PASSES.map((/** @type {any} */ _p, /** @type {number} */ i) => String(i))];
    const at = order.indexOf(current);
    for (const step of el.querySelectorAll('[data-test-step]')) {
      const stepEl = /** @type {HTMLElement} */ (step);
      const index = order.indexOf(stepEl.dataset.testStep || '');
      stepEl.dataset.stepState = index < at ? 'done' : index === at ? 'current' : 'pending';
      if (index !== at) {
        const left = stepEl.querySelector('[data-test-left]');
        if (left) left.textContent = '';
      }
    }
    const pct = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
    const bar = find('[data-test-progress]');
    if (bar) bar.setAttribute('aria-valuenow', String(pct));
    const fillEl = find('[data-test-progress-bar]');
    if (fillEl) fillEl.style.width = `${pct}%`;
  };

  /**
   * The seconds left on a pass's turn.
   *
   * @param {number} index
   * @param {number} seconds
   */
  const secondsLeft = (index, seconds) => {
    const left = find(`[data-test-step="${index}"] [data-test-left]`);
    if (left) left.textContent = fill(STRINGS['seconds-left'], { s: seconds });
  };

  // ---- The run ------------------------------------------------------------

  /**
   * Resolve on the map's next idle (every tile in view loaded and drawn), or
   * false after a cap or when the run ends.
   *
   * @param {number} capMs
   * @param {Run} token
   * @returns {Promise<boolean>}
   */
  const nextIdle = (capMs, token) => new Promise((resolve) => {
    let done = false;
    /** @param {boolean} value */
    const finish = (value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = window.setTimeout(() => finish(false), capMs);
    token.stops.push(() => finish(false));
    map.once('idle', () => finish(true));
    map.triggerRepaint();
  });

  /**
   * Turn the camera at a steady rate and time every animation frame.
   *
   * @param {{turnDeg: number, durationMs: number}} pass
   * @param {Run} token
   * @param {(elapsedMs: number) => void} onFrame
   * @returns {Promise<number[]>} The intervals between frames, in ms.
   */
  const timeTurn = (pass, token, onFrame) => new Promise((resolve) => {
    /** @type {number[]} */
    const intervals = [];
    let last = 0;
    let started = 0;
    let turning = true;
    const end = () => {
      if (!turning) return;
      turning = false;
      resolve(intervals);
    };
    /** @param {number} now */
    const tick = (now) => {
      if (!turning) return;
      if (last) intervals.push(now - last);
      else started = now;
      last = now;
      onFrame(now - started);
      window.requestAnimationFrame(tick);
    };
    map.once('moveend', end);
    token.stops.push(end);
    window.requestAnimationFrame(tick);
    // essential: MapLibre skips the animation under prefers-reduced-motion
    // otherwise, and the pass would end before a frame was timed. The
    // reader asked for this run.
    map.rotateTo(map.getBearing() + pass.turnDeg, {
      duration: pass.durationMs,
      easing: (/** @type {number} */ t) => t,
      essential: true,
    });
  });

  /** @returns {string} The GPU's name, where the browser gives it. */
  const gpuName = () => {
    try {
      const gl = /** @type {any} */ (map).painter.context.gl;
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
    } catch (_err) {
      return '';
    }
  };

  /** A result's overlay key → map.js's overlayState key, where they differ. */
  /** @type {Record<string, string>} */
  const MODE_KEYS = { terrain: 'terrain_filter' };

  /** The budget's two layers' switches, as map.js publishes them. */
  const budgetLayers = () => [
    { key: 'terrain', bridge: win.pwaTerrainFilter },
    { key: 'ski_tours', bridge: win.pwaSkiRoutes ? win.pwaSkiRoutes.tours : null },
  ];

  /**
   * Turn on Steep terrain and SAC tours for a probe run.
   *
   * @returns {() => void} Turns off again what this turned on.
   */
  const switchOnBudgetLayers = () => {
    /** @type {Array<() => void>} */
    const undo = [];
    // Conditions holds both off the map whatever their switches say
    // (map_mode_core.ONLY_IN), so the probe runs in Terrain and puts the
    // reader's mode back after.
    const modes = win.pwaMapMode;
    const before = modes ? modes.get() : '';
    if (modes && !modes.isTerrain()) {
      modes.set('terrain');
      undo.push(() => modes.set(before));
    }
    for (const { bridge } of budgetLayers()) {
      if (!bridge || typeof bridge.isEnabled !== 'function' || bridge.isEnabled()) continue;
      bridge.show();
      undo.push(() => bridge.hide());
    }
    return () => { for (const fn of undo.splice(0).reverse()) fn(); };
  };

  /**
   * Whether the map mode holds an overlay off the map.
   *
   * @param {string} key An OVERLAY_KEYS key.
   * @returns {boolean}
   */
  const heldOff = (key) => {
    const state = win.pwaMapModeState;
    if (!state || typeof state.isSuppressed !== 'function') return false;
    return state.isSuppressed(MODE_KEYS[key] || key);
  };

  /** @returns {string[]} The known switches that are on, in the server's order. */
  const readOverlays = () => {
    const on = new Set();
    for (const row of document.querySelectorAll('#basemap-menu [data-overlay-key][aria-checked="true"]')) {
      on.add(/** @type {HTMLElement} */ (row).dataset.overlayKey);
    }
    for (const { key, bridge } of budgetLayers()) {
      if (bridge && typeof bridge.isEnabled === 'function' && bridge.isEnabled()) on.add(key);
    }
    // What is drawn, not what is switched on: a switch the mode holds off
    // draws nothing.
    return OVERLAY_KEYS.filter((key) => on.has(key) && !heldOff(key));
  };

  /** End the run now going, if one is: no verdict, nothing to send. */
  const cancel = () => {
    const token = run;
    if (!token) return;
    token.cancelled = true;
    run = null;
    for (const stop of token.stops.splice(0)) stop();
    try {
      map.stop();
    } catch (_err) {
      // Nothing was moving.
    }
    if (win.pwaMapView) win.pwaMapView.endTest({});
    token.restoreLayers();
  };

  /**
   * Run the test and show its result.
   *
   * @param {{probe?: boolean}} [options]
   * @returns {Promise<void>}
   */
  const start = async (options = {}) => {
    if (run) return;
    const probe = !!options.probe;
    openPanel();
    result = null;
    report = null;
    probeRun = probe;
    const view = win.pwaMapView;
    const can = view && typeof view.canTest === 'function'
      ? view.canTest()
      : { ok: false, reason: 'device' };
    if (!can.ok) {
      show(can.reason === 'offline' || can.reason === 'editing' ? can.reason : 'unavailable');
      return;
    }
    /** @type {Run} */
    const token = { cancelled: false, stops: [], restoreLayers: () => {} };
    run = token;
    token.restoreLayers = probe ? switchOnBudgetLayers() : () => {};
    const overlays = readOverlays();
    if (!view.beginTest()) {
      run = null;
      token.restoreLayers();
      show('unavailable');
      return;
    }
    // Whatever goes wrong from here, the view and the layers come back and
    // no run is left holding the watchdog off.
    try {
      await runPasses(token, overlays);
    } finally {
      if (run === token) {
        cancel();
        show('unmeasured');
      }
    }
  };

  /**
   * Time each pass, then show the verdict.
   *
   * @param {Run} token
   * @param {string[]} overlays
   * @returns {Promise<void>}
   */
  const runPasses = async (token, overlays) => {
    show('running');
    progress('load', 0);
    await nextIdle(FIRST_IDLE_CAP_MS, token);
    if (token.cancelled) return;
    const firstIdleMs = Math.round(performance.now());
    const total = core.PROBE_PASSES.length;
    /** @type {Array<{label: string, intervals: number[], settled: boolean, settleMs: number}>} */
    const timed = [];
    for (const [i, pass] of core.PROBE_PASSES.entries()) {
      progress(String(i), i / total);
      secondsLeft(i, Math.ceil(pass.durationMs / 1000));
      map.jumpTo({
        center: core.OPENING_CAMERA.center.slice(),
        zoom: pass.zoom,
        pitch: core.OPENING_CAMERA.pitch,
        bearing: core.OPENING_CAMERA.bearing,
      });
      const settleFrom = performance.now();
      const settled = await nextIdle(SETTLE_CAP_MS, token);
      if (token.cancelled) return;
      const settleMs = Math.round(performance.now() - settleFrom);
      let shown = -1;
      const intervals = await timeTurn(pass, token, (elapsed) => {
        const left = Math.max(0, Math.ceil((pass.durationMs - elapsed) / 1000));
        if (left !== shown) {
          shown = left;
          secondsLeft(i, left);
        }
      });
      if (token.cancelled) return;
      timed.push({ label: pass.label, intervals, settled, settleMs });
    }
    finish(token, timed, overlays, firstIdleMs);
  };

  /**
   * Judge the passes, put the map back and show the verdict.
   *
   * @param {Run} token
   * @param {Array<{label: string, intervals: number[], settled: boolean, settleMs: number}>} timed
   * @param {string[]} overlays
   * @param {number} firstIdleMs
   */
  const finish = (token, timed, overlays, firstIdleMs) => {
    const verdict = core.testVerdict(timed.map((pass) => ({ intervals: pass.intervals })));
    // Read while still in 3D: leaving puts the uncapped pixel ratio back,
    // and the canvas grows with it.
    const canvasEl = map.getCanvas();
    const screen = { width: canvasEl.width, height: canvasEl.height, ratio: map.getPixelRatio() };
    const gpu = gpuName();
    const passes = timed.map((pass) => {
      const stats = core.frameStats(pass.intervals);
      const judged = core.judgePass(pass.intervals);
      return { pass, stats, judged };
    });
    run = null;
    win.pwaMapView.endTest({ verdict });
    token.restoreLayers();
    progress(String(core.PROBE_PASSES.length), 1);
    if (verdict === 'unmeasured') {
      show('unmeasured');
      return;
    }
    result = {
      verdict,
      gpu,
      device_pixel_ratio: window.devicePixelRatio || 1,
      pixel_ratio: screen.ratio,
      canvas_width: screen.width,
      canvas_height: screen.height,
      user_agent: navigator.userAgent,
      client_version: clientVersion(),
      probe: probeRun,
      overlays,
      passes: passes.map(({ pass, stats, judged }) => ({
        label: pass.label,
        frames: stats ? stats.frames : 0,
        median_ms: stats ? stats.medianMs : 0,
        p95_ms: stats ? stats.p95Ms : 0,
        worst_ms: stats ? stats.worstMs : 0,
        over_budget: stats ? stats.overBudget : 0,
        slow_windows: judged.slowWindows,
        settle_ms: pass.settleMs,
      })),
    };
    report = {
      probe: 'map_3d SNOW-1456',
      at: new Date().toISOString(),
      budgetMs: core.FRAME_BUDGET_MS,
      firstIdleMs,
      ...result,
      passes: passes.map(({ pass, stats, judged }) => ({
        label: pass.label,
        settled: pass.settled,
        settleMs: pass.settleMs,
        keptUp: judged.keptUp,
        windows: judged.windows,
        slowWindows: judged.slowWindows,
        ...stats,
      })),
    };
    show(verdict);
    paintResult(passes, gpu, screen);
    const details = /** @type {?HTMLDetailsElement} */ (find('[data-test-details]'));
    if (details && probeRun) details.open = true;
  };

  /**
   * Each pass's line and figures, and the device's.
   *
   * @param {Array<{pass: any, stats: any, judged: any}>} passes
   * @param {string} gpu
   * @param {{width: number, height: number, ratio: number}} screen The
   *   canvas and pixel ratio the passes were drawn at, read in 3D.
   */
  const paintResult = (passes, gpu, screen) => {
    passes.forEach(({ stats, judged }, i) => {
      const row = find(`[data-test-pass="${i}"]`);
      if (row) {
        for (const status of row.querySelectorAll('[data-pass-status]')) {
          const keeps = /** @type {HTMLElement} */ (status).dataset.passStatus === 'keeps';
          /** @type {HTMLElement} */ (status).hidden = keeps !== judged.keptUp;
        }
      }
      const figures = find(`[data-test-figures="${i}"]`);
      if (!figures || !stats) return;
      /** @type {Record<string, string>} */
      const lines = {
        median: fill(STRINGS['figure-median'], { ms: stats.medianMs.toFixed(1) }),
        fps: fill(STRINGS['figure-fps'], { fps: stats.fps }),
        p95: fill(STRINGS['figure-p95'], { ms: stats.p95Ms.toFixed(1) }),
        over: fill(STRINGS['figure-over'], { pct: `${Math.round(stats.overBudget * 100)}%`, budget: core.FRAME_BUDGET_MS }),
        windows: fill(STRINGS['figure-windows'], { slow: judged.slowWindows, windows: judged.windows }),
        settle: fill(STRINGS['figure-settle'], { s: (passes[i].pass.settleMs / 1000).toFixed(1) }),
      };
      for (const [key, text] of Object.entries(lines)) {
        const cell = figures.querySelector(`[data-figure="${key}"]`);
        if (cell) cell.textContent = text;
      }
    });
    const gpuEl = find('[data-test-gpu]');
    if (gpuEl) gpuEl.textContent = fill(STRINGS['figure-gpu'], { gpu: gpu || STRINGS['gpu-unknown'] });
    const screenEl = find('[data-test-screen]');
    if (screenEl) {
      const { ratio } = screen;
      screenEl.textContent = fill(STRINGS['figure-screen'], {
        width: Math.round(screen.width / ratio),
        height: Math.round(screen.height / ratio),
        dpr: Number((window.devicePixelRatio || 1).toFixed(2)),
        ratio: Number(ratio.toFixed(2)),
      });
    }
    const ruleEl = find('[data-test-rule]');
    if (ruleEl) {
      ruleEl.textContent = fill(STRINGS['figure-rule'], {
        budget: core.FRAME_BUDGET_MS,
        n: core.FRAME_WINDOWS_TO_TRIP,
        s: core.FRAME_WINDOW_MS / 1000,
      });
    }
  };

  // ---- Send and Share -----------------------------------------------------

  /** @returns {string} The page's build, as pwa_client_version.js reads it. */
  const clientVersion = () => {
    const meta = document.querySelector('meta[name="pwa-app-version"]');
    return meta ? (meta.getAttribute('content') || '').trim() : '';
  };

  /** @returns {string} The page's CSRF token. */
  const csrfToken = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input instanceof HTMLInputElement ? input.value : '';
  };

  /** Post the result once; 204 is sent, anything else is not. */
  const send = async () => {
    if (!result || sending || !SEND_URL) return;
    sending = true;
    const payload = result;
    const failed = find('[data-test-send-failed]');
    if (failed) failed.hidden = true;
    let ok = false;
    try {
      const response = await fetch(SEND_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(payload),
      });
      ok = response.status === 204;
    } catch (_err) {
      ok = false;
    }
    sending = false;
    // The panel closed, or the test ran again, while the post was out.
    if (result !== payload) return;
    if (ok) {
      result = null;
      const box = find('[data-test-send-box]');
      if (box) box.hidden = true;
      const sentEl = find('[data-test-sent]');
      if (sentEl) sentEl.hidden = false;
      return;
    }
    const failedNow = find('[data-test-send-failed]');
    if (failedNow) failedNow.hidden = false;
  };

  /** Share the result: the full JSON on a probe run, a few lines otherwise. */
  const share = async () => {
    if (!report) return;
    const text = probeRun
      ? JSON.stringify(report, null, 1)
      : [
        STRINGS[`verdict-${report.verdict}`],
        ...report.passes.map((/** @type {any} */ pass, /** @type {number} */ i) => fill(STRINGS['share-line'], {
          label: STRINGS[`pass-${i}`] || pass.label,
          status: pass.keptUp ? STRINGS['keeps-up'] : STRINGS['falls-behind'],
          median: Number(pass.medianMs).toFixed(1),
        })),
        fill(STRINGS['figure-gpu'], { gpu: report.gpu || STRINGS['gpu-unknown'] }),
      ].join('\n');
    const sharer = win.pwaShare;
    const outcome = sharer ? await sharer.shareOrCopy(text, STRINGS['share-title'], 'text') : 'failed';
    const status = find('[data-test-share-status]');
    if (!status) return;
    if (outcome === 'copied') status.textContent = STRINGS.copied;
    else if (outcome === 'failed') status.textContent = STRINGS['copy-failed'];
  };

  sheetEl.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest('[data-action="map-3d-test-send"]')) send();
    else if (target.closest('[data-test-share]')) share();
    else if (target.closest('[data-test-again], [data-action="map-3d-test-again"]')) {
      // After this click is done: start() replaces the panel's body, and the
      // detached button would read as a tap outside to MapSheet's own
      // click-outside, which would close the panel and end the new run.
      const probe = probeRun;
      setTimeout(() => { start({ probe }); }, 0);
    }
  });

  // ---- What asks for it, and what ends it -------------------------------

  document.addEventListener('snowdesk:map-3d-test-requested', () => { start(); });

  // A hidden tab draws nothing, and its gap is not a slow frame.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden || !run) return;
    cancel();
    sheet.close();
  });

  // Leaving 3D mid-run (the 2D switch, going offline, panning out) would
  // time the rest of the run flat, so the run ends with no verdict.
  document.addEventListener('snowdesk:map-view-changed', (event) => {
    const detail = /** @type {CustomEvent} */ (event).detail;
    if (!run || !detail || detail.view === '3d') return;
    cancel();
    sheet.close();
  });

  map.on('webglcontextlost', () => {
    if (!run) return;
    cancel();
    sheet.close();
  });

  if (new URLSearchParams(location.search).get('probe') === '1') {
    const loaded = typeof map.loaded !== 'function' || map.loaded();
    if (loaded) start({ probe: true });
    else map.once('load', () => { start({ probe: true }); });
  }
})();
