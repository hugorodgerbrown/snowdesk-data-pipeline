// @ts-check
/*
 * static/js/map_relief_tuning.js — the staff panel that tunes the relief
 * past the Swiss border, on /map/.
 *
 * Loaded only for a staff reader in the map_beyond_switzerland flag
 * (public/map.html, `relief_tuning`), after map_beyond_switzerland.js. It
 * does nothing unless `#map` carries data-relief-tuning and the panel
 * (public/partials/_map_relief_tuning_panel.html) is on the page.
 *
 * ASKED FOR by snowdesk:map-relief-tuning-requested, the "?" menu's "Tune
 * the relief" (map_help_menu.js). MapSheet.attach empties the sheet on every
 * close, so each open clones #map-relief-tuning-template and fills its four
 * sliders from window.pwaBeyondSwitzerland.tuning().
 *
 * A SLIDER APPLIES LIVE. Its readout changes at once; TUNE_DEBOUNCE_MS after
 * the last move the four values go to pwaBeyondSwitzerland.tune (the
 * relief worker repaints, the tiles get a new key) and are saved on this
 * device under pwaBeyondSwitzerland.TUNING_STORAGE_KEY, which
 * map_beyond_switzerland.js reads back on the next visit. A close mid-wait
 * applies what is pending. Reset tunes to alpine_relief_core's
 * TUNING_DEFAULTS and forgets the saved values.
 *
 * The rock slope is in degrees; the three strengths are shown 0–100 % and
 * handed over as 0..1.
 */
(function mapReliefTuningInit() {
  'use strict';

  const mapEl = document.getElementById('map');
  const sheetEl = document.getElementById('map-relief-tuning-sheet');
  const template = document.getElementById('map-relief-tuning-template');
  const reliefCore = self.pwaAlpineReliefCore;
  const win = /** @type {any} */ (window);
  if (!mapEl || mapEl.dataset.reliefTuning === undefined) return;
  if (!sheetEl || !(template instanceof HTMLTemplateElement) || !reliefCore || !win.MapSheet) return;

  /** How long after the last slider move the relief is repainted. */
  const TUNE_DEBOUNCE_MS = 150;

  /** The four values, and whether each is a 0..1 strength shown in %. */
  const FIELDS = Object.freeze([
    { name: 'rockSlope', percent: false },
    { name: 'rockStrength', percent: true },
    { name: 'shadowStrength', percent: true },
    { name: 'highlightStrength', percent: true },
  ]);

  /** @type {number} */
  let timer = 0;
  /**
   * The sliders' values at the last move, read then: a close empties the
   * sheet before its teardown applies them.
   *
   * @type {?Record<string, number>}
   */
  let pending = null;

  /** @returns {any} The runtime that owns the relief, once it has booted. */
  const api = () => window.pwaBeyondSwitzerland || null;

  /** @returns {string} */
  const storageKey = () => {
    const owner = api();
    return owner ? owner.TUNING_STORAGE_KEY : 'snowdesk.map.reliefTuning';
  };

  /**
   * @param {string} name
   * @returns {?HTMLInputElement}
   */
  const input = (name) => /** @type {?HTMLInputElement} */ (sheetEl.querySelector(`[data-relief-input="${name}"]`));

  /**
   * What a slider shows beside its label.
   *
   * @param {boolean} percent
   * @param {number} value The slider's own value.
   * @returns {string}
   */
  const readoutText = (percent, value) => (percent ? `${value} %` : `${value}°`);

  /** Write each slider's readout from its value. */
  const showReadouts = () => {
    for (const { name, percent } of FIELDS) {
      const el = input(name);
      const readout = sheetEl.querySelector(`[data-relief-readout="${name}"]`);
      // i18n-allow: staff-only tuning panel
      if (el && readout) readout.textContent = readoutText(percent, Number(el.value));
    }
  };

  /**
   * Set the sliders to a tuning.
   *
   * @param {Readonly<Record<string, number>>} tuning
   */
  const fill = (tuning) => {
    for (const { name, percent } of FIELDS) {
      const el = input(name);
      if (el) el.value = String(Math.round(percent ? tuning[name] * 100 : tuning[name]));
    }
    showReadouts();
  };

  /** @returns {Record<string, number>} The sliders' tuning. */
  const read = () => {
    /** @type {Record<string, number>} */
    const values = {};
    for (const { name, percent } of FIELDS) {
      const el = input(name);
      if (el) values[name] = percent ? Number(el.value) / 100 : Number(el.value);
    }
    return values;
  };

  /** Hand the last move's tuning to the relief and keep it on this device. */
  const apply = () => {
    clearTimeout(timer);
    timer = 0;
    const values = pending;
    pending = null;
    const owner = api();
    if (!owner || !values) return;
    const tuning = owner.tune(values);
    try {
      window.localStorage.setItem(storageKey(), JSON.stringify(tuning));
    } catch (_err) {
      // No storage: the tuning holds for this visit only.
    }
  };

  /** Apply a move still waiting on its debounce. */
  const flush = () => {
    if (pending) apply();
  };

  /** Back to the defaults, and forget the saved tuning. */
  const reset = () => {
    clearTimeout(timer);
    timer = 0;
    pending = null;
    const owner = api();
    if (owner) owner.tune(reliefCore.TUNING_DEFAULTS);
    try {
      window.localStorage.removeItem(storageKey());
    } catch (_err) {
      // Nothing saved to forget.
    }
    fill(reliefCore.TUNING_DEFAULTS);
  };

  const sheet = win.MapSheet.attach(sheetEl, {
    // What opens it is not a click outside it.
    triggerSelector: '[data-help-menu="relief-tuning"]',
    onTeardown: flush,
  });

  /** Open the panel on a fresh body, showing the tuning in use. */
  const open = () => {
    sheetEl.replaceChildren(template.content.cloneNode(true));
    const owner = api();
    fill(owner ? owner.tuning() : reliefCore.TUNING_DEFAULTS);
    if (!sheet.isOpen()) sheet.open();
  };

  sheetEl.addEventListener('input', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !target.matches('[data-relief-input]')) return;
    showReadouts();
    pending = read();
    clearTimeout(timer);
    timer = window.setTimeout(apply, TUNE_DEBOUNCE_MS);
  });

  sheetEl.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target && target.closest('[data-action="relief-tuning-reset"]')) reset();
  });

  document.addEventListener('snowdesk:map-relief-tuning-requested', open);
})();
