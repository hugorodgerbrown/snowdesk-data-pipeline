/*
 * static/js/day_strips.js — moves the day strips' selected block
 * (SNOW-1219).
 *
 * templates/includes/_day_strips.html renders every block's figures and
 * readings, with the server's choice selected. This module moves the
 * selection when a block button is pressed or a plot is tapped or dragged:
 * it writes `--block` on the section (the capsule and the bands follow)
 * and flips `data-selected` / `aria-pressed` on the block-indexed
 * elements. It writes no text, so every word stays server-rendered.
 *
 * Delegated from the document, because the map's weather sheet injects the
 * strips after load and re-injects them on every symbol tap; nothing needs
 * binding per render.
 *
 * Depends on day_strips_core.js, loaded before it.
 */

(function dayStripsInit() {
  'use strict';

  var core = self.pwaDayStripsCore;
  if (!core) return;

  /**
   * Select block `index` in one strips section.
   *
   * @param {Element} section The `[data-day-strips]` element.
   * @param {number} index The block, 0-7.
   */
  function select(section, index) {
    if (!(section instanceof HTMLElement)) return;
    section.style.setProperty('--block', String(index));
    section.querySelectorAll('[data-block]').forEach(function (el) {
      var on = Number(el.getAttribute('data-block')) === index;
      el.toggleAttribute('data-selected', on);
      if (el.hasAttribute('aria-pressed')) {
        el.setAttribute('aria-pressed', on ? 'true' : 'false');
      }
    });
    section.querySelectorAll('[data-boundary]').forEach(function (el) {
      var at = Number(el.getAttribute('data-boundary'));
      el.toggleAttribute('data-selected', at === index || at === index + 1);
    });
  }

  /**
   * Select the block under a pointer on a plot.
   *
   * @param {Element} plot A `[data-day-strips-plot]` element.
   * @param {number} clientX The pointer's x.
   */
  function selectAt(plot, clientX) {
    var rect = plot.getBoundingClientRect();
    var index = core.blockAt(clientX, rect.left, rect.width);
    var section = plot.closest('[data-day-strips]');
    if (index !== null && section) select(section, index);
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!(target instanceof Element)) return;
    var button = target.closest('[data-day-strips] button[data-block]');
    if (!button) return;
    var section = button.closest('[data-day-strips]');
    if (section) select(section, Number(button.getAttribute('data-block')));
  });

  /** @type {?Element} */
  var dragging = null;

  document.addEventListener('pointerdown', function (event) {
    var target = event.target;
    if (!(target instanceof Element)) return;
    var plot = target.closest('[data-day-strips-plot]');
    if (!plot) return;
    dragging = plot;
    selectAt(plot, event.clientX);
  });

  document.addEventListener('pointermove', function (event) {
    if (dragging) selectAt(dragging, event.clientX);
  });

  ['pointerup', 'pointercancel'].forEach(function (type) {
    document.addEventListener(type, function () {
      dragging = null;
    });
  });
})();
