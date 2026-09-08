/*
 * static/js/reset_data_summary_core.js — what "Reset local data" is about
 * to delete, grouped by what it costs to lose (SNOW-860).
 *
 * `/account/settings/`'s Reset local data row asks the user to approve a
 * deletion whose contents they could not see: `pwa_reset.js` unregisters
 * every service worker, deletes every Cache Storage bucket, drops every
 * IndexedDB database and clears both web-storage areas, and the only
 * thing between the user and that was a `window.confirm` quoting three
 * words. This module is the arithmetic behind the breakdown that replaced
 * them; `reset_data_summary.js` is the DOM half and does nothing this one
 * could do instead.
 *
 * ## Grouped by cost, not by storage API
 *
 * The four categories are not `caches` / `indexedDB` / `localStorage` /
 * service workers. A user does not know or care which API holds what;
 * they care whether losing it costs them a re-download over mobile data,
 * a piece of work that never reached the server, a page reload, or a
 * preference they will re-set in five seconds. So:
 *
 *   - **Downloaded maps** — the areas the user chose, plus the shared
 *     overview maps the app fetched for itself. Expensive: re-downloading
 *     needs a connection, and the whole point of them is being somewhere
 *     without one.
 *   - **Unsent changes** — rows still in `queue:mutations`. Not bytes, a
 *     COUNT, and the only category where a non-zero figure is a warning:
 *     these have not reached the server, so the reset is the last thing
 *     that will ever happen to them.
 *   - **Cached pages and tiles** — everything else the origin is holding.
 *     Cheap: losing it costs a reload, not data.
 *   - **Preferences** — basemap choice, camera, panel states, telemetry
 *     opt-in. No size worth stating; the list itself is the disclosure.
 *
 * ## Two figures that need explaining
 *
 * **Downloaded-map bytes are the RECORDED figure**, the one each
 * completed download run wrote — never a live measurement of the bucket.
 * `map_basemap_downloads.js`'s `measurePinnedBucketBytes` sums
 * `Content-Length`, and the tile origin sends none under gzip, so it
 * reads ~0 in production for a bucket full of real tiles. Using it here
 * would have shipped a breakdown claiming 0 MB of maps on a device
 * holding 400 of them — and passed a test suite whose fixture Responses
 * carry no `Content-Length` either.
 *
 * **Cached bytes are a subtraction, and approximate.**
 * `navigator.storage.estimate()` reports one figure for the whole origin,
 * so the honest attribution is "everything the browser says we are using,
 * minus what we can account for as downloaded maps". Floored at zero,
 * because the two numbers come from different places and a browser that
 * reports less than we recorded must not produce a negative row. Where
 * `estimate()` is unavailable the category still renders — with no
 * figure, and the "costs you a reload" framing, which is the part that
 * matters.
 *
 * Pure: no DOM, no fetch, no storage. Everything arrives as an argument.
 */

(function () {
  'use strict';

  /**
   * A finite, non-negative byte count from an untrusted value.
   *
   * @param {*} value
   * @returns {number}
   */
  function bytesOf(value) {
    var n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  /**
   * The downloaded-map items, in the order the panel lists them.
   *
   * The user's own areas first, the shared overview maps last. They are
   * different in kind — one set was chosen, the other was fetched by the
   * app for itself and is neither listed nor budgeted for by the Manage
   * downloads sheet (SNOW-867) — and the shared ones are the surprise, so
   * they read better as an addition to a list the user recognises than
   * interleaved into it.
   *
   * @param {Array<{id: string, label?: string, bytes?: number,
   *   onDevice?: boolean}>} rows `pwaBasemapManageCore.manageRows` output.
   *   A row with `onDevice === false` exists on the ACCOUNT only — another
   *   device's download, or one evicted here — so this reset cannot delete
   *   it and it must not be counted.
   * @param {Array<{id: string, name?: string, bytes?: number}>} baseLayers
   *   The shared overview maps, as `basemapDownloadedAreas()` returns
   *   them. `bytes` is 0 when the record has not landed yet (SNOW-863);
   *   the row still appears, because the tiles are on the device whether
   *   or not their size was recorded.
   * @returns {Array<{id: string, label: string, bytes: number,
   *   shared: boolean}>}
   */
  function mapItems(rows, baseLayers) {
    var items = [];
    (Array.isArray(rows) ? rows : []).forEach(function (row) {
      if (!row || !row.id || row.onDevice === false) return;
      items.push({
        id: String(row.id),
        label: String(row.label || row.id),
        bytes: bytesOf(row.bytes),
        shared: false,
      });
    });
    (Array.isArray(baseLayers) ? baseLayers : []).forEach(function (layer) {
      if (!layer || !layer.id) return;
      items.push({
        id: String(layer.id),
        label: String(layer.name || layer.id),
        bytes: bytesOf(layer.bytes),
        shared: true,
      });
    });
    return items;
  }

  /**
   * The whole breakdown, from what the page managed to read.
   *
   * Every input is optional and every missing one degrades to a stated
   * absence rather than a thrown error: this panel sits beside a recovery
   * control, and a device broken enough to fail these reads is exactly
   * the device whose user is reaching for it.
   *
   * @param {{rows?: Array<Object>, baseLayers?: Array<Object>,
   *   mutationCount?: number, storageEstimate?: {usage?: number}|null}}
   *   input `rows` and `baseLayers` are described on `mapItems` above.
   *   `mutationCount` is the depth of the `queue:mutations` store —
   *   SNOW-376's pending mutations, NOT `queue:events`, which is the
   *   SNOW-385 telemetry buffer and would put a count of analytics
   *   payloads under a heading reading "unsent changes".
   *   `storageEstimate` is `navigator.storage.estimate()`'s result, or
   *   null where the browser has no such method.
   * @returns {{maps: {items: Array<Object>, bytes: number},
   *   unsent: {count: number, warn: boolean},
   *   cached: {bytes: number|null, known: boolean},
   *   totalBytes: number, totalIsPartial: boolean}}
   *   `cached.known` is false when the origin's usage could not be read;
   *   `totalIsPartial` says the same thing about the total, which is then
   *   the downloaded maps alone and is understating.
   */
  function summarise(input) {
    var source = input || {};
    var items = mapItems(source.rows, source.baseLayers);
    var mapBytes = items.reduce(function (sum, item) {
      return sum + item.bytes;
    }, 0);

    var usage = source.storageEstimate ? Number(source.storageEstimate.usage) : NaN;
    var usageKnown = Number.isFinite(usage) && usage >= 0;
    // Floored at 0: the recorded download figure and the browser's own
    // accounting are two different measurements of overlapping ground, and
    // a "-12 MB of cached pages" row would be worse than saying nothing.
    var cachedBytes = usageKnown ? Math.max(0, usage - mapBytes) : null;

    var count = Number(source.mutationCount);
    if (!Number.isFinite(count) || count < 0) count = 0;

    return {
      maps: { items: items, bytes: mapBytes },
      unsent: { count: count, warn: count > 0 },
      cached: { bytes: cachedBytes, known: usageKnown },
      totalBytes: mapBytes + (cachedBytes || 0),
      totalIsPartial: !usageKnown,
    };
  }

  window.pwaResetDataSummaryCore = Object.freeze({
    summarise: summarise,
  });
})();
