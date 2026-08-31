/**
 * static/js/hourly_chart_legend.js — open/close for the hourly chart's legend.
 *
 * The chart itself is server-rendered and completely static: no hover, no
 * tooltip, no scrubber, nothing to boot. It is read like a printed
 * meteogram, and it has to survive being screenshotted into a group chat
 * with nothing lost. This file is the *only* JavaScript the component has,
 * and all it does is show and hide one panel.
 *
 * Consequences worth keeping:
 *
 *   - The chart is fully readable with JS disabled or still loading. The
 *     legend is the one thing that is not, which is the right thing to
 *     lose: it explains the marks, it does not carry the forecast.
 *   - There are no user-facing strings here. Everything the legend says is
 *     rendered by includes/_hourly_chart_legend.html, where makemessages
 *     can see it — a string built in JS ships as English to every locale.
 *
 * Delegated from the document, so a chart swapped in by HTMX works without
 * re-initialising anything.
 */

(function () {
  "use strict";

  var OPEN = "[data-hourly-chart-legend-open]";
  var CLOSE = "[data-hourly-chart-legend-close]";
  var PANEL = "[data-hourly-chart-legend]";
  var BACKDROP = "[data-hourly-chart-legend-backdrop]";

  /** Elements that can hold focus inside the open panel. */
  var FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

  /** The trigger that opened the panel currently showing, if any. */
  var openTrigger = null;

  /**
   * Show or hide one chart's legend.
   *
   * @param {Element} root The chart root.
   * @param {boolean} show Whether the panel should be visible.
   */
  function setOpen(root, show) {
    var panel = root.querySelector(PANEL);
    var backdrop = root.querySelector(BACKDROP);
    var trigger = root.querySelector(OPEN);
    if (!panel) return;

    [panel, backdrop].forEach(function (el) {
      if (!el) return;
      el.hidden = !show;
      el.classList.toggle("hidden", !show);
    });
    if (trigger) trigger.setAttribute("aria-expanded", show ? "true" : "false");

    if (show) {
      openTrigger = trigger;
      var first = panel.querySelector(FOCUSABLE);
      if (first) first.focus();
    } else {
      if (openTrigger) openTrigger.focus();
      openTrigger = null;
    }
  }

  /** Close every open legend on the page. */
  function closeAll() {
    document.querySelectorAll(PANEL).forEach(function (panel) {
      var root = panel.closest("[role='group']");
      if (root && !panel.hidden) setOpen(root, false);
    });
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!(target instanceof Element)) return;

    var opener = target.closest(OPEN);
    if (opener) {
      var root = opener.closest("[role='group']");
      if (root) setOpen(root, true);
      return;
    }
    if (target.closest(CLOSE) || target.closest(BACKDROP)) closeAll();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    closeAll();
  });

  // Keep focus inside an open panel: a modal the keyboard can walk out of
  // behind is a modal in appearance only.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Tab") return;
    var panel = document.querySelector(PANEL + ":not([hidden])");
    if (!panel) return;
    var items = Array.prototype.slice.call(panel.querySelectorAll(FOCUSABLE));
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
