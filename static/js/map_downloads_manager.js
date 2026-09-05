/*
 * static/js/map_downloads_manager.js — the "Manage downloads" sheet
 * (SNOW-588; the way in rewritten by SNOW-634; SNOW-635 adds Rename and
 * lets more than one custom area be listed).
 *
 * The DOM half of the surface that answers "what have I stored offline on
 * this device, and what is it costing me". Every piece of arithmetic and
 * ordering lives in static/js/basemap_manage_core.js, which is unit-tested
 * directly; this file is what turns that into a sheet, and deliberately
 * holds no logic worth testing without a DOM.
 *
 * Markup: apps/public/templates/public/partials/_map_downloads_sheet.html.
 * Opened from ``#map-custom-download-control`` (the bottom-right roundel —
 * public/partials/_map_custom_download_control.html), driven by that
 * button's own click handler in map.js calling ``window.pwaDownloadsManager
 * .open()`` (this module's own bridge — see the bridges section below).
 * SNOW-588 through SNOW-632 reached this sheet only via a
 * ``[data-menu-action="manage-downloads"]`` row buried in the layers menu,
 * while the roundel opened a DIFFERENT surface directly (the "Download a
 * custom area" framing overlay); SNOW-634 collapsed the two — that menu
 * row is gone, and "Download a custom area" moved INTO this sheet instead,
 * as the ``[data-panel-add]`` trigger handled below.
 *
 * ## What it reads and writes
 *
 * Everything here is device-local by nature: Cache Storage is per-browser,
 * so the same account on a phone and a laptop has two independent sets of
 * downloads and two independent budgets.
 *
 * The downloads themselves live in TWO ``meta:app`` rows, and this module
 * reads NEITHER directly — it goes through ``map.js``'s
 * ``basemapDownloadedAreas()`` (see the bridges section below):
 *
 *   basemap.regions      An array, one entry per downloaded region, keyed
 *                        by ``region_id``. Written by the per-region
 *                        download control.
 *   basemap.customAreas  An array (SNOW-635 — was a single row through
 *                        SNOW-634), one entry per user-framed area.
 *                        Written by the custom-area control.
 *
 * Neither key is the Cache Storage bucket id — that is
 * ``areaIdForRegion(region_id)`` or the custom area's own minted id
 * (``generateCustomAreaId`` / ``isCustomAreaId``) — which is the single
 * most important reason not to read them here. An earlier version of this
 * module read a ``basemap.areas`` row that no writer has ever produced,
 * and every test passed, because the tests seeded that row themselves. The
 * area list and the delete now both go through the code the download
 * controls and the eviction planner already use, so this surface cannot
 * disagree with them about what is on disk.
 *
 *   basemap.budgetMb    The standing budget, read by SNOW-586's eviction
 *                       planner. This surface is the only thing that ever
 *                       writes it, and the one row it touches directly.
 *
 * ## Deleting is a bucket delete, not a tile sweep
 *
 * SNOW-586 gives every download its own cache
 * (``snowdesk-basemap-pinned-<areaId>``), so removing one is a single
 * ``caches.delete()`` plus the matching record entry — atomic per area,
 * where the custom-area control previously had to re-derive the old URL
 * set from ``buildBlob`` and delete entry by entry, silently leaving
 * orphan tiles if it failed part-way. That is why this ticket depends on
 * that one: an itemised list with a working delete is only honest if
 * deleting cannot half-succeed.
 *
 * Both halves are ``map.js``'s ``evictBasemapAreas``, which this module
 * calls rather than reimplements. Deleting an area means taking an area
 * id back to the right half of the right record — the region array or the
 * custom-area array — and the eviction path already does exactly that, for
 * the budget planner. A second implementation here would be the same
 * decision made twice, and the one that ran less often would be the one
 * that rotted.
 *
 * ``evictBasemapAreas`` is best-effort per area and reports nothing, so
 * the outcome is confirmed by re-reading the area list and checking the
 * id is gone — which is a stronger check than a return value anyway: it
 * verifies the state, not the attempt.
 *
 * ## Renaming (SNOW-635)
 *
 * A custom area's default label is "Custom area N" — filled into
 * ``area.name`` by ``map.js``'s ``basemapDownloadedAreas()`` itself, from
 * the record's ``ordinal``, in memory only and never persisted (storing
 * the interpolated string would freeze the user's language at download
 * time). By the time a row reaches this module, ``row.label`` is already
 * the right text to show — stored name or numbered default, uniformly —
 * so this module needs no ``ordinal``-aware fallback of its own; it just
 * renders ``row.label``.
 *
 * SNOW-658 (Hugo's map-panel design): renaming happens IN PLACE on the
 * row's label, opened by the row's PENCIL — the label itself was a second
 * trigger for a day and is inert again (see static/js/inline_rename.js's
 * ``rowFor``). The pencil reveals the editor the row template already
 * rendered; ``window.pwaInlineRename`` owns the
 * interaction — shared with the favourites panel, which renames the same
 * way — and this module supplies only the commit:
 * ``window.pwaBasemapDownloads.rename(areaId, name)`` (map.js's own
 * bridge — see the bridges section below), then a re-render. The
 * ``window.prompt`` this replaces was defended as "no new markup, no
 * focus trap, no Escape handling"; the design's answer is that a modal
 * browser dialogue covering the row it renames is not a saving.
 *
 * Only a ``renameable`` row (a custom area with a real record behind it,
 * per ``basemap_manage_core.js``'s ``manageRows``) shows the pencil or
 * carries the editor — a region's name is its real name, and an orphaned
 * bucket has no record entry left to write one onto. ``buildRow`` strips
 * both from every other row.
 *
 * ## Why it re-clones its body on every open
 *
 * The sheet is rebuilt from its ``<template>`` each time it opens rather
 * than being updated in place. Downloads and evictions both happen while
 * this sheet is closed (a download run, or SNOW-586 evicting to make room),
 * so anything cached in the DOM between opens is a stale row waiting to be
 * shown. Rebuilding is a few dozen nodes and removes the whole class of
 * bug.
 *
 * ## Offline gating is applied at render time (SNOW-637)
 *
 * Everything this sheet does to what is ALREADY stored — listing, sizing,
 * renaming, deleting — works offline, and must: storage pressure is felt
 * exactly when there is no connection to relieve it. Starting a NEW
 * download is the one thing that cannot, so ``[data-panel-add]`` is
 * disabled and relabelled while the app is not using the network, keeping the
 * control visible rather than hiding it (the treatment
 * map_layer_sync_status.js established for uncached rows offline). SNOW-748:
 * "not using the network" is ``networkInUse()``, not ``navigator.onLine`` —
 * a mode the user forced from the account menu's "Offline mode" row leaves
 * the interface up and the network out of bounds.
 *
 * That gating lives inside ``render()`` because of the re-clone above: a
 * listener or attribute bound to the trigger at boot is thrown away with
 * the previous body on the next open. A ``snowdesk:connectivity-changed``
 * listener re-renders an OPEN sheet so a connection dropped mid-use lands
 * immediately, and the click handler keeps its own ``networkInUse()``
 * check as the race guard for the gap between paint and tap.
 *
 * ## Its map.js dependencies are four narrow bridges
 *
 * Everything this module needs from static/js/map.js is module scope
 * there, so it reaches it through four small frozen globals that file
 * exposes:
 *
 *   window.pwaDownloadedOverlay  (SNOW-645 review) ``show()``, ``hide()``
 *                                and ``isEnabled()`` for the downloaded-
 *                                tiles map overlay (SNOW-658 review: its
 *                                ``isVisible()`` answers from the drawn
 *                                layers instead, which is the roundel
 *                                ring's question, not this switch's).
 *                                ``open()`` calls
 *                                ``show()`` unconditionally; the in-sheet
 *                                "Available offline" toggle's own change
 *                                handler is the ONLY thing that ever calls
 *                                ``hide()`` — closing the sheet does not,
 *                                because it is bottom-docked and
 *                                full-width on mobile
 *                                (includes/_overlay_sheet.html), and
 *                                binding visibility to "sheet is open"
 *                                left the squares this overlay draws
 *                                permanently covered by the thing showing
 *                                them. ``render()`` reads ``isVisible()``
 *                                back to paint the toggle, rather than
 *                                keeping a flag of its own that could
 *                                drift from it.
 *   window.pwaBasemapDownloads   ``areas()``, ``evict(ids)``, (SNOW-635)
 *                                ``rename(areaId, name)``, (SNOW-645)
 *                                ``basemapLabel(key)`` and (SNOW-832)
 *                                ``basemapOrder()`` — the read, the
 *                                delete, the rename, and the two halves
 *                                of a group heading: what a basemap is
 *                                called and where it sits in the picker's
 *                                own order. Without it the sheet opens
 *                                and honestly reports that it can see
 *                                nothing, which is the truthful answer
 *                                when the module that owns the records
 *                                hasn't loaded.
 *                                SNOW-832 dropped the one call this
 *                                module made to that bridge's
 *                                ``orphanBasemapKey(areaId)``: it existed
 *                                to tint an orphaned row's left-edge
 *                                rule, and identity moved to the group
 *                                heading, which is a claim about
 *                                belonging that an inference may not
 *                                make.
 *   window.pwaMapOverlays        ``register()`` + ``opening()`` (SNOW-658).
 *                                This sheet used to close ONE named
 *                                sibling on its way in
 *                                (``window.pwaLayersMenu.close()``) and
 *                                nothing else, and nothing closed it. It
 *                                now registers with the shared map-overlay
 *                                registry
 *                                (static/js/map_overlay_exclusivity.js)
 *                                and announces its own open, so it closes
 *                                every other overlay and every other
 *                                overlay closes it, without either naming
 *                                the other. Optional — without it the
 *                                overlays simply stop being exclusive.
 *   window.pwaCustomAreaDownload SNOW-634's third bridge: ``openFraming()``,
 *                                called by ``[data-panel-add]`` below
 *                                once this sheet has hidden itself. Optional
 *                                — without it the trigger is a dead click,
 *                                which is no worse than the sheet failing
 *                                to open at all when its OWN bridge is
 *                                missing.
 *
 * The budget row (``basemap.budgetMb``) is the one piece of storage this
 * module touches directly, because it is the only thing that ever writes
 * it; ``map.js``'s ``basemapDownloadBudgetBytes()`` is the reader.
 *
 * ## Its one non-map dependency (SNOW-749)
 *
 * ``window.pwaDownloadsSync`` (static/js/downloads_sync.js) — the account
 * half. Three uses: the trash calls ``forget()`` alongside the local
 * eviction, ``isEnabled()`` decides nothing on its own any more (see
 * ``_handleDeleteClick``, which keys its copy off the ROW), and this
 * module owns the one call to ``adopt()``, on DOMContentLoaded. That last
 * one is here rather than in ``downloads_sync.js`` itself because a module
 * that self-invokes on load is a module whose behaviour cannot be reasoned
 * about from its call sites — and because this file already computes the
 * flag-and-session pair the call is conditional on. Optional throughout:
 * without it the sheet is exactly what it was before that ticket.
 */

(function mapDownloadsManagerInit() {
  'use strict';

  var SHEET_ID = 'map-downloads-sheet';
  var BODY_TEMPLATE_ID = 'map-downloads-body-template';
  var ROW_TEMPLATE_ID = 'map-downloads-row-template';

  // The one storage row this module touches directly. The downloads
  // themselves live in two records read through window.pwaBasemapDownloads
  // (see the module header); this is the only one nothing else writes.
  var BUDGET_KEY = 'basemap.budgetMb';

  var sheet = document.getElementById(SHEET_ID);
  var bodyTemplate = document.getElementById(BODY_TEMPLATE_ID);
  var rowTemplate = document.getElementById(ROW_TEMPLATE_ID);
  if (!sheet || !bodyTemplate || !rowTemplate) return;

  /**
   * SNOW-748: true when the app is actually using the network — the interface
   * is up AND no offline mode is in force.
   *
   * The gating below used ``navigator.onLine``, which stays true under a mode
   * the user forced from the account menu's "Offline mode" row: the sheet went
   * on offering "Add an area" while the header symbol said the app was offline
   * and the worker refused the run. ``pwa_offline.js`` owns the answer, and its
   * ``snowdesk:connectivity-changed`` broadcast (listened for below) is what
   * re-renders an open sheet when it changes.
   *
   * @returns {boolean}
   */
  function networkInUse() {
    var connectivity = window.pwaConnectivity;
    return connectivity ? connectivity.isOnline() : navigator.onLine !== false;
  }

  /**
   * Translated strings, lifted out of the strings template.
   *
   * ``makemessages`` only scans templates and Python, so every string this
   * module renders is server-rendered into a hidden template and read back
   * here rather than written as a JS literal — which would ship
   * untranslatable English to every locale.
   *
   * SNOW-620: this module was the reference implementation, and its reader
   * is now the shared one in static/js/i18n_strings.js — eight more
   * surfaces needed it, and its two non-obvious details (whitespace
   * collapsed rather than trimmed; substitution by name rather than
   * position) are exactly the kind that get lost in a re-implementation.
   *
   * @type {Object<string, string>}
   */
  var STRINGS = self.pwaStrings.read('map-downloads-strings-template', {
    // SNOW-645 review: an orphaned row (SNOW-612) is labelled by what it
    // IS — the leftovers of a download that never finished — with no
    // resume affordance (considered and dropped: a custom-area orphan has
    // no record to rebuild from, and a link that silently does nothing
    // for one of the two row kinds is worse than no link for either).
    // "Incomplete download" shortened to "Incomplete" in a later review
    // pass, matching Hugo's second mock.
    'kind-incomplete': 'Incomplete',
    // SNOW-832: what KIND of download a row is. It used to be said by the
    // heading the row sat under (SNOW-645's REGIONS / CUSTOM AREAS); the
    // headings now name basemaps, so the kind moved into the row's own
    // meta line beside the size — "Region · 5.1 MB". The separator lives
    // inside `row-meta` rather than being concatenated here, matching how
    // every server-rendered meta line on the other panels is written
    // (favourites' "{{ region }} · saved {{ when }}").
    'kind-region': 'Region',
    'kind-custom': 'Custom area',
    'row-meta': '%(kind)s · %(size)s',
    // SNOW-832: the group of rows whose basemap cannot be named — a
    // record written before SNOW-645, an orphaned bucket, an account row
    // with no basemap. Named rather than left blank: an unlabelled
    // heading over real rows reads as a rendering fault.
    'group-unknown': 'Unknown basemap',
    // SNOW-646/832: the budget bar's accessible equivalent. Segmented by
    // basemap, the bar states a composition the panel says nowhere else,
    // so it is a `role="img"` with a description rather than decoration.
    // Built per render from budgetSegments() — see render().
    'budget-bar-label': 'Space used, by base map: %(segments)s',
    'budget-bar-empty': 'Nothing downloaded on this device',
    'budget-segment': '%(basemap)s %(size)s',
    'confirm-remove':
      "Remove the offline map for %(name)s? This frees %(size)s. You can " +
      "download it again when you're back online.",
    'remove-failed': "That download couldn't be removed. Try again.",
    // SNOW-634: [data-panel-add]'s offline refusal.
    'add-offline': "You're offline — connect to download a new area.",
    // SNOW-637: the same refusal as the disabled trigger's own label — the
    // toast above is phrased as a reply to a tap, which reads wrong on a
    // control nobody has touched yet.
    'add-disabled': 'Downloading needs a connection',
    // SNOW-749: the same pair again for the signed-out refusal. Two more
    // strings rather than reusing the offline pair, because the reason is
    // different and the remedy is different — one waits for a signal, the
    // other is one tap away — and a control that says the wrong thing about
    // why it will not run is worse than one that says nothing.
    'add-signin': 'Sign in to download a new area.',
    'add-signin-disabled': 'Downloading needs an account',
    // SNOW-658: the rename PROMPT's copy is gone with the prompt — the
    // inline editor's aria-label is server-rendered on the row itself.
    // Only the failure line is still written from here. The default
    // "Custom area N" label lives in map.js's MAP_STRINGS
    // (_map_embed.html), not here — see this module's "Renaming" header
    // note for why.
    'rename-failed': "That name couldn't be saved. Try again.",
    // SNOW-658: each row action names the row it acts on. "Rename" alone
    // names nothing with a list of areas on screen — the same reason the
    // "…" trigger this replaces carried a per-row label. The favourite and
    // observation rows interpolate theirs in the template; a row cloned
    // from a <template> has no name at render time, so it is done here.
    'rename-row-label': 'Rename %(name)s',
    'remove-row-label': 'Remove %(name)s',
    // SNOW-811: the row's NAME frames its area, as on the other three
    // panels. Same reason as the two above for being interpolated here.
    'focus-row-label': 'Zoom to %(name)s',
    // SNOW-749: an area on the account that this device does not hold.
    // The subtitle replaces the basemap name for such a row — which
    // basemap it was fetched under elsewhere is a fact about a device the
    // reader is not looking at, while "not here" is the reason the row
    // looks different.
    'not-on-device': 'On your account — not downloaded here',
    'download-here-row-label': 'Download %(name)s to this device',
    // SNOW-832 removed `free-space-row-label` and `confirm-free-space`
    // with the control they belonged to — see
    // includes/_map_downloads_row_actions.html for why the second
    // destructive verb went. The two confirmations below are the whole
    // set again: one for a row with an account row behind it, one for a
    // row without.
    'confirm-forget':
      'Remove %(name)s from your account and from this device? This frees ' +
      '%(size)s here and removes it from your other devices too.',
    'download-here-failed': "That download couldn't be started here. Try again.",
  });

  var interpolate = self.pwaStrings.interpolate;

  // SNOW-749: the sign-in gate's configuration, read off the roundel that
  // opens this sheet (#map-custom-download-control — see its own template
  // comment for why that one element carries the wiring for every download
  // surface). Read once at parse time: the session is fixed for the life
  // of the document, unlike `navigator.onLine`, which is why the offline
  // branch below is re-evaluated on every render and this is not.
  var DOWNLOADS_CONFIG_EL = document.getElementById('map-custom-download-control');
  var DOWNLOADS_CONFIG = DOWNLOADS_CONFIG_EL ? DOWNLOADS_CONFIG_EL.dataset : {};
  var DOWNLOADS_ELIGIBLE = DOWNLOADS_CONFIG.downloadsEligible === 'true';
  var DOWNLOADS_SIGNIN_URL = DOWNLOADS_CONFIG.signinUrl || '';
  // Authentication is the whole gate. SNOW-749 briefly paired this with a
  // `downloadsSync` rollout-flag attribute; the flag was dropped before
  // merge on query cost, and with it the only state in which "gate off"
  // and "gate passed" were different things.
  //
  // A page with no config element applies no gate and does not sync — see
  // `_adoptLocalAreas` for why that matters there specifically.
  var NEEDS_SIGNIN = !!DOWNLOADS_CONFIG_EL && !DOWNLOADS_ELIGIBLE;

  /**
   * Send the visitor to sign in.
   *
   * A navigation rather than an in-sheet CTA panel: this sheet's list is
   * still true and still useful for a signed-out visitor — everything on
   * it is on THIS device and reading it is never gated — so replacing the
   * body with a gate would take away something that works to explain
   * something that does not. Only the add-trigger changes.
   *
   * @returns {void}
   */
  function goToSignIn() {
    if (DOWNLOADS_SIGNIN_URL) window.location.assign(DOWNLOADS_SIGNIN_URL);
  }

  /** @returns {Object|null} The pure core, once it has loaded. */
  function manageCore() {
    return window.pwaBasemapManageCore || null;
  }

  /** @returns {Object|null} basemap_download_core.js's exports. */
  function downloadCore() {
    return window.pwaBasemapDownloadCore || null;
  }

  // SNOW-645: `basemapLabel` (the picker-label lookup used by `buildRow`
  // below) now lives in static/js/map_basemap_downloads.js, shared with
  // the region roundel's "downloaded under another basemap" state
  // (map_region_download.js) — a second copy here would be the same
  // lookup written twice. Called lazily (only once the sheet is actually
  // opened), so it is safe to reference as a bare identifier regardless of
  // this file's own position in the document relative to that one's
  // <script> tag: by the time a user can open this sheet, every deferred
  // script on the page — including map_basemap_downloads.js — has already
  // run. tests/public/test_map_script_order.py's own docstring lists this
  // file as one of the eight map*.js modules deliberately outside
  // MAP_BUNDLE's load-order contract, which only binds modules that need
  // each other AT PARSE TIME.

  /**
   * Read a ``meta:app`` row's value (the budget row — see ``BUDGET_KEY``).
   *
   * Best-effort: IndexedDB can be unavailable (private mode, a blocked
   * upgrade — see db.js's ``reset_required``), and a manage surface that
   * throws on open is worse than one that falls back to the default
   * budget.
   *
   * @param {string} key
   * @returns {Promise<any>} ``undefined`` when absent or unreadable.
   */
  async function readMeta(key) {
    try {
      const row = await window.pwaDb?.get('meta:app', key);
      return row ? row.value : undefined;
    } catch (_err) {
      return undefined;
    }
  }

  /**
   * Write a ``meta:app`` row.
   *
   * @param {string} key
   * @param {any} value
   * @returns {Promise<boolean>} Whether the write landed — the budget
   *   control needs to know, since a silently-dropped budget would leave
   *   the select showing a setting the eviction planner never sees.
   */
  async function writeMeta(key, value) {
    try {
      await window.pwaDb?.put('meta:app', { key: key, value: value });
      return true;
    } catch (_err) {
      return false;
    }
  }

  /**
   * Every recorded area, via map.js's normalising reader.
   *
   * @returns {Promise<Array<Object>>} Empty when the bridge is absent or
   *   the read fails — the sheet then honestly reports an empty device
   *   rather than throwing on open.
   */
  async function readAreas() {
    try {
      const areas = await window.pwaBasemapDownloads?.areas();
      return Array.isArray(areas) ? areas : [];
    } catch (_err) {
      return [];
    }
  }

  // SNOW-749: area ids forgotten from the ACCOUNT during this session.
  //
  // The forget is a queued mutation, so the account row survives the
  // re-render that follows the tap — sometimes by milliseconds, sometimes
  // (offline) until the device next has signal. Without this the row the
  // user just removed reappears immediately, which reads as the removal
  // having failed. The set is the optimistic half of the same shape the
  // favourites panel's pending pin uses, and it is deliberately
  // session-scoped: the next page load re-reads the truth, by which point
  // the queue has drained.
  const FORGOTTEN = new Set();

  /**
   * Evict one area from THIS DEVICE — its pinned bucket and its record.
   *
   * Delegates to map.js's ``evictBasemapAreas``, which already knows how
   * to take an area id back to the right half of the right record (the
   * ``basemap.regions`` array or the ``basemap.customAreas`` array). It is
   * best-effort and returns nothing, so the outcome is confirmed by
   * re-reading the area list — verifying the state rather than trusting
   * the attempt.
   *
   * The LOCAL half of the trash, and only that: it leaves the account row
   * alone, so ``forgetArea`` below is what pairs it with the account
   * removal. It was also SNOW-749's "free up space" control, on its own —
   * that control is gone (SNOW-832), and this is deliberately unchanged,
   * because it is the same call SNOW-586's automatic budget eviction
   * makes and an area evicted THERE must still survive as an account row
   * to re-download.
   *
   * @param {string} areaId
   * @returns {Promise<boolean>} Whether the area's local copy is now
   *   genuinely gone.
   */
  async function evictArea(areaId) {
    if (!window.pwaBasemapDownloads) return false;
    try {
      await window.pwaBasemapDownloads.evict([areaId]);
    } catch (_err) {
      return false;
    }

    const remaining = await readAreas();
    // An area still listed as ON THIS DEVICE means the eviction did not
    // land. One listed as account-only did land — that is the row this
    // verb is meant to leave behind, not a failure.
    if (remaining.some((area) => area && area.id === areaId && area.onDevice !== false)) {
      return false;
    }

    // evictBasemapAreas already refreshes the downloaded-areas overlay.
    // The layers-menu dots are this module's to refresh: they claim the
    // basemap is available offline, which one fewer pinned bucket can
    // change.
    window.pwaLayerSyncStatus?.refresh();
    // SNOW-658 removed a `window.pwaCustomAreaDownload?.refresh()` here.
    // SNOW-634 added it so a delete could take the roundel that opens this
    // sheet from "done" back to "idle"; that state is gone (the roundel
    // says whether its overlay is on the map, not what is on disk), and
    // this sheet's own re-render is what reports the delete.
    return true;
  }

  /**
   * Remove one area EVERYWHERE — this device's copy and the account row.
   *
   * The trash's verb. The account half is queued (so it survives a tap
   * made with no signal) and the local half is immediate, which is why the
   * id is remembered in ``FORGOTTEN`` rather than the row being trusted to
   * disappear from the next read.
   *
   * Ordered account-first so that a row which is ONLY on the account —
   * nothing here to evict — still has its trash do something. The local
   * eviction is skipped entirely for such a row: there is no bucket, and
   * ``evictArea``'s verification would have nothing to verify.
   *
   * @param {string} areaId
   * @param {boolean} onDevice Whether this device holds the area's tiles.
   * @returns {Promise<boolean>} Whether the removal is complete as far as
   *   this device can tell.
   */
  async function forgetArea(areaId, onDevice) {
    FORGOTTEN.add(areaId);
    await window.pwaDownloadsSync?.forget(areaId);
    if (!onDevice) {
      // Nothing local to remove, so nothing local to verify.
      window.pwaLayerSyncStatus?.refresh();
      return true;
    }
    return evictArea(areaId);
  }

  /**
   * Populate the budget ``<select>`` from the core's offered choices.
   *
   * Built here rather than server-rendered so the offered sizes live in
   * one place — next to the floor that pins the smallest of them to
   * ``DOWNLOAD_CEILING_MB``. The labels are numerals plus "MB", so
   * generating them costs no translatable text.
   *
   * @param {HTMLSelectElement} select
   * @param {number} selectedMb
   */
  function renderBudgetOptions(select, selectedMb) {
    const core = manageCore();
    if (!core) return;
    select.textContent = '';
    for (const mb of core.BUDGET_CHOICES_MB) {
      const option = document.createElement('option');
      option.value = String(mb);
      option.textContent = core.formatMegabytes(core.megabytesToBytes(mb));
      if (mb === selectedMb) option.selected = true;
      select.appendChild(option);
    }
  }

  /**
   * Rebuild the sheet's contents from the current records.
   *
   * @returns {Promise<void>}
   */
  async function render() {
    const core = manageCore();
    if (!core) return;

    const [allAreas, budgetMb] = await Promise.all([
      readAreas(),
      readMeta(BUDGET_KEY),
    ]);
    // SNOW-749: drop anything the user has forgotten this session whose
    // queued mutation has not drained yet — see FORGOTTEN's own comment.
    // An area forgotten and then RE-DOWNLOADED here is back on the device,
    // so it is listed again: the local record is newer than the pending
    // forget, and the user is looking at the thing they just made.
    const list = allAreas.filter(
      (area) => !FORGOTTEN.has(area.id) || area.onDevice !== false,
    );
    const chosenMb = core.clampBudgetMb(budgetMb);
    const summary = core.budgetSummary(list, core.megabytesToBytes(chosenMb));
    const rows = core.manageRows(list, {
      // SNOW-635: a predicate, not a single id — see manageRows's own
      // docstring for why. No `customLabel` any more either — a custom
      // row's label is `area.name`, already filled by the reader.
      isCustomAreaId: downloadCore()?.isCustomAreaId,
    });

    // SNOW-832 removed a pre-pass here. SNOW-645 resolved every orphaned
    // row's basemap by INFERENCE (map_basemap_downloads.js's
    // `orphanBasemapKey`, which matches an orphan bucket's own cached
    // tile URLs against the templates on record) purely to give its
    // left-edge rule a pale version of that colour instead of a flat
    // neutral.
    //
    // That rule is gone: basemap identity belongs to the GROUP HEADING
    // now, and a group is a claim about which basemap a download BELONGS
    // to — which is exactly the claim `orphanBasemapKey`'s own docstring
    // forbids its answer from making ("decoration only, never a stored
    // fact"). So an orphan keeps its empty `basemapKey` and lands in the
    // unnamed group, which is the group for rows whose basemap is not
    // recorded. Its own "Incomplete" meta line still says what it is.

    // Re-clone rather than update in place — see the module header.
    sheet.textContent = '';
    sheet.appendChild(bodyTemplate.content.cloneNode(true));

    // SNOW-645 review: reflects the overlay's REAL state —
    // window.pwaDownloadedOverlay — rather than a flag of its own that could
    // drift from it.
    //
    // SNOW-658 review: isEnabled(), the persisted preference that show()/
    // hide() write — NOT isVisible(), which now answers from the squares
    // MapLibre is drawing. Same split as the other two panels' own switches:
    // this states what the user asked for. The two diverge while something
    // else has cleared the map (a placement flow, and this sheet is not open
    // then), and — since the overlay draws the ACTIVE basemap's downloads
    // alone — whenever the overlay is on and this basemap has none, where ON
    // over an empty map is the honest reading.
    //
    // SNOW-645 review (SAST): selected by id, not a data-attribute hook —
    // includes/_switch.html dropped its extra_attrs passthrough (a live
    // attribute-injection surface semgrep flagged), and the switch already
    // renders id="{{ id }}", which this sheet already sets to the one
    // fixed value below — a second hook was never needed.
    const overlayToggle = /** @type {HTMLInputElement|null} */ (
      sheet.querySelector('#map-downloads-overlay-toggle')
    );
    if (overlayToggle) {
      overlayToggle.checked = !!window.pwaDownloadedOverlay?.isEnabled?.();
    }

    // SNOW-645 review: the budget figure itself is no longer stated in
    // this text — it now lives in the <select> right after it ("40.3 MB
    // of [500 MB ⌄]"), part of the same header row (see the sheet's own
    // template comment for the layout). The used FIGURE and the word "of"
    // are two separately-styled spans now, reconciled against the design
    // (number+unit in mono/medium/text-1, "of" in plain text-2) — only
    // the figure needs JS at all: `core.formatMegabytes()` is a
    // locale-invariant numeral, not translatable text, so it is set
    // directly rather than through the STRINGS/interpolate path "of"
    // itself still uses as a static `{% trans %}` node in the template.
    const summaryValue = sheet.querySelector('[data-downloads-summary-value]');
    if (summaryValue) summaryValue.textContent = core.formatMegabytes(summary.usedBytes);

    const segments = core.budgetSegments(list);

    const bar = sheet.querySelector('[data-downloads-bar]');
    if (bar) {
      bar.style.width = summary.pct + '%';
      bar.textContent = '';
      // SNOW-645 review: one segment per basemap actually stored (grouped
      // and summed by basemap_manage_core.js's budgetSegments — never one
      // per area), each sized by flex-grow in proportion to its own share
      // of the used bytes and coloured via .basemap-identity-fill's
      // data-basemap-key rules (src/css/main.css §2) — the same rules the
      // row swatches use, so the two readings of "what basemap is this"
      // agree. JS sets the key and the grow weight; nothing here touches
      // a colour or builds a class string.
      for (const segment of segments) {
        const el = document.createElement('div');
        el.className = 'basemap-identity-fill h-full';
        el.dataset.basemapKey = segment.basemapKey;
        el.style.flexGrow = String(segment.bytes);
        el.style.flexShrink = '0';
        el.style.flexBasis = '0';
        bar.appendChild(el);
      }
    }

    const track = sheet.querySelector('[data-downloads-track]');
    if (track) {
      // The bar itself can no longer turn solid red once it is carrying
      // real basemap colours (SNOW-645 review) — the over-budget signal
      // moves to the track that holds it instead. Still toggled, not a
      // one-way class: coming back under budget (a delete, or raising the
      // budget) has to clear it on the next render.
      track.classList.toggle('ring-2', summary.overBudget);
      track.classList.toggle('ring-status-error-text', summary.overBudget);
      // SNOW-646/832: the bar's accessible equivalent. The template marks
      // it `role="img"`; the description is built here because it is the
      // composition of THIS device's downloads, which no static string
      // can state. Each segment is named with its share — the same list
      // that paints the bar, in the same order, so what a screen reader
      // hears and what a sighted reader sees cannot disagree.
      //
      // Not a `progressbar`: the reading is "what is in it", not "how far
      // along". The "Using X of Y" sentence beside it already carries the
      // proportion, which is why this one carries only the parts.
      track.setAttribute('aria-label', budgetBarLabel(segments));
    }

    const over = sheet.querySelector('[data-downloads-over]');
    if (over) over.hidden = !summary.overBudget;

    const empty = sheet.querySelector('[data-downloads-empty]');
    if (empty) empty.hidden = rows.length > 0;

    // SNOW-832: "Display on the map" governs the squares this panel's
    // downloads draw. With nothing downloaded there are no squares, so the
    // switch is a control that cannot do what it says — and a user who
    // turns it on and sees no change learns the wrong thing about it.
    // Hidden, not disabled: unlike the add-trigger's offline state there
    // is nothing here to explain, and the switch comes back the moment
    // there is a download for it to show. The hook is on the whole strip
    // (includes/_map_overlay_toggle.html) so its label and its box go with
    // it rather than an empty box being left behind.
    const overlayStrip = sheet.querySelector('[data-panel-overlay-toggle]');
    if (overlayStrip) overlayStrip.hidden = rows.length === 0;

    // SNOW-832: rows are grouped by BASEMAP, each group under a heading
    // carrying that basemap's own identity colour, in the order the
    // picker offers them (basemapOrder() — see its own docstring for why
    // the order is read off the picker's DOM rather than declared here).
    // This replaces SNOW-645's two fixed REGIONS / CUSTOM AREAS wrappers;
    // a row's kind is now its own meta line (see buildRow).
    //
    // groupRowsByBasemap groups manageRows' own regions-then-custom,
    // alphabetical order without re-sorting it, and never returns an
    // empty group — so there is no hidden-wrapper state to manage here,
    // unlike the two wrappers this replaces.
    const groupHost = sheet.querySelector('[data-downloads-groups]');
    // Resolved from the CLONED body, not from the document: the group
    // template is nested inside the body template (it belongs to this
    // panel's rows partial), and a `<template>`'s contents are not in the
    // document, so `document.getElementById` would never find it. Deep-
    // cloning a `<template>` clones its own content fragment with it,
    // which is what makes the nesting work at all.
    const groupTemplate = /** @type {HTMLTemplateElement|null} */ (
      sheet.querySelector('#map-downloads-group-template')
    );
    if (groupHost && groupTemplate) {
      for (const group of core.groupRowsByBasemap(rows, basemapOrder())) {
        groupHost.appendChild(buildGroup(groupTemplate, group, core));
      }
    }

    const select = /** @type {HTMLSelectElement|null} */ (
      sheet.querySelector('[data-downloads-budget]')
    );
    if (select) renderBudgetOptions(select, chosenMb);

    // SNOW-637: offline-integrity for the add-trigger. Listing and deleting
    // what is already stored needs no connection, but STARTING a download
    // does, so the control is disabled and relabelled rather than left live
    // to fail on tap — the disabled-but-visible treatment
    // map_layer_sync_status.js already established for uncached rows
    // offline, not a second one.
    //
    // Applied here rather than bound once at init because the body is
    // re-cloned from its <template> on every render (see the module
    // header), which discards anything bound to the previous copy. Only the
    // offline branch needs writing for the same reason: the next render
    // starts from a pristine clone, so coming back online restores the
    // enabled control and its original label without an else.
    //
    // SNOW-749 adds the signed-out branch alongside it, and puts it FIRST:
    // a signed-out visitor with no connection cannot download for two
    // reasons, and the account is the one they can act on now. Unlike the
    // offline branch this one is NOT disabled — the control stays live and
    // its tap goes to sign-in, because a gate the visitor can pass is an
    // affordance, not a dead end. Only the label changes.
    const addButton = sheet.querySelector('[data-panel-add]');
    if (addButton && NEEDS_SIGNIN) {
      addButton.textContent = STRINGS['add-signin-disabled'] || '';
    } else if (addButton && !networkInUse()) {
      addButton.setAttribute('disabled', '');
      // Alongside the native property, not instead of it: `disabled` is
      // what stops the click, `aria-disabled` is what a screen reader
      // announces before the user reaches for it.
      addButton.setAttribute('aria-disabled', 'true');
      addButton.textContent = STRINGS['add-disabled'] || '';
    }
  }

  /**
   * The picker's basemap order, through map.js's own bridge (SNOW-832).
   *
   * Reached through ``window.pwaBasemapDownloads`` rather than as a bare
   * identifier for the same load-order reason ``basemapLabel`` is — see
   * the note above ``readMeta``. Optional throughout: with no bridge (or
   * an older cached shell whose bridge predates this ticket) the groups
   * fall back to first-appearance order, which is an order, just not the
   * curated one.
   *
   * @returns {string[]}
   */
  function basemapOrder() {
    return window.pwaBasemapDownloads?.basemapOrder?.() || [];
  }

  /**
   * A basemap key's own name, or the "cannot be named" string (SNOW-832).
   *
   * One place, because the group heading and the budget bar's description
   * both have to answer it and must answer it the same way — a bar
   * segment called "Unknown basemap" over a group heading left blank
   * would read as two different facts.
   *
   * @param {string} key '' for the keyless group.
   * @returns {string}
   */
  function basemapName(key) {
    const label = key
      ? window.pwaBasemapDownloads?.basemapLabel?.(key) || ''
      : '';
    // Also covers a key the PICKER no longer offers (a deployment
    // BASEMAP= override, a style since retired): the rows are real and
    // still deletable, so they get the honest heading rather than a raw
    // key nobody outside this codebase has seen.
    return label || STRINGS['group-unknown'] || '';
  }

  /**
   * The budget bar's accessible description (SNOW-646/832).
   *
   * @param {Array<{basemapKey: string, bytes: number}>} segments As
   *   ``budgetSegments`` returns them — largest first, keyless last, which
   *   is the order the bar paints, so this describes the bar rather than
   *   the record behind it.
   * @returns {string} A whole sentence, never a bare list: a `role="img"`
   *   with a fragment for a name is worse than one with none.
   */
  function budgetBarLabel(segments) {
    const core = manageCore();
    if (!core || !segments.length) return STRINGS['budget-bar-empty'] || '';
    const parts = segments.map((segment) =>
      interpolate(STRINGS['budget-segment'], {
        basemap: basemapName(segment.basemapKey),
        size: core.formatMegabytes(segment.bytes),
      }),
    );
    // ', ' is punctuation, not prose — the two translatable halves are the
    // segment format above and the sentence below.
    return interpolate(STRINGS['budget-bar-label'], { segments: parts.join(', ') });
  }

  /**
   * Build one basemap group: its heading, its identity colour and its rows
   * (SNOW-832).
   *
   * @param {HTMLTemplateElement} template The group `<template>`, resolved
   *   from the cloned body — see render()'s own comment for why it cannot
   *   be resolved from the document.
   * @param {{basemapKey: string, rows: Array<Object>, totalBytes: number}}
   *   group As ``groupRowsByBasemap`` returns it.
   * @param {Object} core ``pwaBasemapManageCore``, for the byte formatting.
   * @returns {DocumentFragment}
   */
  function buildGroup(template, group, core) {
    const fragment = /** @type {DocumentFragment} */ (
      template.content.cloneNode(true)
    );

    const label = fragment.querySelector('[data-hook="group-label"]');
    if (label) label.textContent = basemapName(group.basemapKey);

    // What THIS DEVICE holds for the group. An account-only row counts 0
    // (it costs this device nothing — the same reason it is out of the
    // budget), so a group's total can read smaller than its rows suggest.
    // That is the truth this panel exists to tell: listed is not the same
    // as available offline.
    const total = fragment.querySelector('[data-group-total]');
    if (total) total.textContent = core.formatMegabytes(group.totalBytes);

    // The identity colour, on both marks at once — the round swatch
    // before the label and the rule under the whole heading line. Both
    // branches are written, rather than one being left to the template's
    // own default: a keyless group must NOT fall through to
    // `.basemap-identity-fill`'s keyless green, which means "downloaded,
    // basemap unknown" and would give these rows a colour identity they
    // do not have. `bg-sync-off` — the "absent, not an error" grey — is
    // the same call SNOW-749 made for an account-only row's rule.
    for (const mark of fragment.querySelectorAll(
      '[data-group-swatch], [data-group-rule]',
    )) {
      if (group.basemapKey) {
        mark.classList.remove('bg-sync-off');
        mark.classList.add('basemap-identity-fill');
        mark.dataset.basemapKey = group.basemapKey;
      } else {
        mark.classList.remove('basemap-identity-fill');
        mark.classList.add('bg-sync-off');
      }
    }

    const rows = fragment.querySelector('[data-group-rows]');
    if (rows) {
      for (const row of group.rows) rows.appendChild(buildRow(row));
    }
    return fragment;
  }

  /**
   * Stamp a cloned row's label with what the map should frame (SNOW-811).
   *
   * The row template renders the `<button>` label with an EMPTY
   * `data-row-focus`, the same value-less-here shape the rename and delete
   * hooks use; this fills it, per row, after the clone.
   *
   * Two shapes, because a downloaded area is recorded two ways. A custom
   * area carries its own `bbox`, which is exactly what
   * `includes/_ugc_panel_row.html`'s `focus_target` slot expects — four
   * comma-separated ordinates, west,south,east,north. A region carries a
   * `regionId` and NO bbox on purpose (`reconcileAreas`: its tiles come
   * from the real boundary server-side, so a box would be a second,
   * coarser answer), so it is stamped on `data-row-focus-region` instead
   * and resolved against the map's own polygon at press time.
   *
   * `toFixed(6)` rather than plain interpolation, matching the `%f` the
   * server's `{% focus_target %}` writes for the other three panels: six
   * decimals is ~11cm, and a fixed shape means one attribute format for
   * `row_focus.js` to parse whether the row came from a template or from
   * here. (`Number.prototype.toFixed` is locale-independent — this is not
   * the `toLocaleString` trap `map_focus.py` exists to avoid.)
   *
   * A row with NEITHER — an orphaned bucket (SNOW-612), which has no
   * record left to name a region or a box — has both attributes removed,
   * so `row_focus.js`'s `[data-row-focus]` selector does not match it and
   * the click falls through to the other row controls. Its affordance
   * classes go with them: a label that cannot be pressed must not look
   * pressable. Remove is the only action an orphan gets, and that is still
   * true.
   *
   * @param {HTMLElement} label The cloned row's `[data-row-label]`.
   * @param {{label: string, orphaned?: boolean, bbox?: number[]|null,
   *   regionId?: string}} row
   * @returns {void}
   */
  function applyRowFocus(label, row) {
    const bbox = Array.isArray(row.bbox) && row.bbox.length === 4
      ? row.bbox
      : null;
    const framable = bbox
      ? bbox.every((n) => Number.isFinite(Number(n)))
      : Boolean(row.regionId);

    if (!framable) {
      label.removeAttribute('data-row-focus');
      label.removeAttribute('aria-label');
      label.classList.remove('hover-affordance');
      return;
    }

    if (bbox) {
      label.setAttribute(
        'data-row-focus',
        bbox.map((n) => Number(n).toFixed(6)).join(','),
      );
    } else {
      label.removeAttribute('data-row-focus');
      label.setAttribute('data-row-focus-region', row.regionId);
    }
    label.setAttribute(
      'aria-label',
      interpolate(STRINGS['focus-row-label'], { name: row.label }),
    );
  }

  /**
   * Build one list row.
   *
   * The title is the row's plain name (SNOW-645 review reversed an
   * earlier "Verbier (Region)" fold-in), and everything a row says about
   * itself beyond that is one meta line: "Region · 5.1 MB" (SNOW-832),
   * "Incomplete" for an orphan, "On your account — not downloaded here"
   * for a row this device does not hold.
   *
   * SNOW-832 took two things OFF the row. The coloured left-edge rule
   * carrying the basemap identity moved up to the group heading, which
   * says it once for a group instead of once per row; and the trailing
   * size column folded into the meta line above, which is why the row
   * template no longer renders ``[data-row-value]`` for a JS-filled row.
   *
   * @param {{id: string, kind: string, orphaned?: boolean, label: string,
   *   renameable?: boolean, size: string, basemapKey?: string}} row
   * @returns {DocumentFragment}
   */
  function buildRow(row) {
    const fragment = /** @type {DocumentFragment} */ (
      rowTemplate.content.cloneNode(true)
    );

    // SNOW-635 review: `row.label` is already the right text — a stored
    // name, or an unrenamed custom area's numbered default, filled in
    // upstream by map.js's basemapDownloadedAreas() — so this module has
    // no ordinal-aware fallback of its own to build any more. For an
    // orphaned row (SNOW-612) it is the bare bucket id — manageRows falls
    // back to `id` only when there is no record at all — which is exactly
    // what buildRow shows: an orphan is labelled by what it IS.
    //
    // SNOW-645 review: an orphaned row's title dims to `text-text-2` (the
    // template's own default is `text-text-1`) — reconciled against the
    // design, which dims the WHOLE row (title, size, and the rule's own
    // opacity below), not just the rule, for a row that never finished.
    const label = fragment.querySelector('[data-row-label]');
    if (label) {
      label.textContent = row.label;
      // SNOW-749: an account-only row dims exactly as an orphan does. Both
      // are "listed, but not offline here", which is what the dimming has
      // always meant on this sheet — the difference between them is the
      // subtitle and the controls, not the weight.
      if (row.orphaned || row.onDevice === false) {
        label.classList.remove('text-text-1');
        label.classList.add('text-text-2');
      }
      applyRowFocus(label, row);
    }

    // SNOW-832: the meta line is "<kind> · <size>" — "Region · 5.1 MB".
    // It used to name the row's BASEMAP, which the group heading above it
    // now says once for the whole group instead of once per row; the kind
    // came the other way, down from SNOW-645's REGIONS / CUSTOM AREAS
    // headings.
    //
    // Two rows say something else entirely, and keep the whole line for
    // it, because in both cases the kind and the size are not the fact
    // worth stating:
    //
    //   an ORPHAN (SNOW-612)  "Incomplete", with no link. SNOW-645 review
    //     considered a "resume" affordance and dropped it: a region
    //     orphan's bucket id could in principle drive one, but a
    //     custom-area orphan has no record — no bbox, no band — to
    //     rebuild from, and a link that silently does nothing for one of
    //     the two row kinds is worse than no link for either. Remove is
    //     the only action an orphan gets.
    //   an ACCOUNT-ONLY row (SNOW-749)  "On your account — not downloaded
    //     here". It costs this device nothing, so a size would be "0.0 MB"
    //     — a download that somehow takes no space rather than one that is
    //     not here.
    const subtitle = fragment.querySelector('[data-row-meta]');
    if (subtitle) {
      if (row.orphaned) {
        subtitle.textContent = STRINGS['kind-incomplete'] || '';
      } else if (row.onDevice === false) {
        subtitle.textContent = STRINGS['not-on-device'] || '';
      } else {
        subtitle.textContent = interpolate(STRINGS['row-meta'], {
          kind:
            (row.kind === 'custom'
              ? STRINGS['kind-custom']
              : STRINGS['kind-region']) || '',
          size: row.size,
        });
      }
      // No per-branch dimming here any more (SNOW-832). SNOW-645 dimmed
      // the SIZE COLUMN to `text-text-3` for an orphan, alongside the
      // title and the rule; the size lives on this line now, and the
      // line's own default already IS `text-text-3`. The title's drop to
      // `text-text-2` above is what still carries "this row is not
      // available offline".
    }

    const button = fragment.querySelector('[data-downloads-delete]');
    if (button) {
      button.setAttribute('data-downloads-delete', row.id);
      // Carried on the element so the delegated handler can name the area
      // in its confirmation without re-reading the record.
      button.setAttribute('data-downloads-label', row.label);
      button.setAttribute('data-downloads-size', row.size);
      // SNOW-749: and the row's own two facts, so the handler knows
      // whether there is anything local to evict alongside the account row
      // — and whether there is an account row at all. Both are read off
      // the ROW rather than off a global: a download made before the flag
      // was opened, or one whose sync push has not landed, is
      // `synced: false` on a page where the sync feature is switched on
      // and working, and the confirmation must not promise to remove it
      // from other devices it was never on.
      button.setAttribute('data-downloads-on-device', String(row.onDevice !== false));
      button.setAttribute('data-downloads-synced', String(!!row.synced));
      // SNOW-658: and the control names the row it acts on. A server-
      // rendered panel interpolates this in its own template; a row cloned
      // from a <template> has no name until here.
      button.setAttribute(
        'aria-label',
        interpolate(STRINGS['remove-row-label'], { name: row.label }),
      );
    }

    // SNOW-749: "Download here" — the only constructive action a row has,
    // and the reason an account-only row is listed at all. Stripped from
    // every row already on this device (nothing to fetch) and from an
    // account-only row carrying too little to act on — `redownloadable`
    // is false without a region id or a bbox, and a control that silently
    // does nothing is worse than no control.
    const hereBtn = fragment.querySelector('[data-downloads-here]');
    if (hereBtn) {
      if (row.redownloadable) {
        hereBtn.setAttribute('data-downloads-here', row.id);
        hereBtn.setAttribute('data-downloads-region', row.regionId || '');
        // The box travels as JSON on the element rather than through a
        // closure, for the same reason every other value here does: the
        // handler is delegated on the sheet and the row is a clone.
        hereBtn.setAttribute(
          'data-downloads-bbox',
          Array.isArray(row.bbox) ? JSON.stringify(row.bbox) : '',
        );
        hereBtn.setAttribute(
          'aria-label',
          interpolate(STRINGS['download-here-row-label'], { name: row.label }),
        );
      } else {
        hereBtn.remove();
      }
    }

    // SNOW-635: only a renameable row (a custom area with a real record
    // behind it — see manageRows's own docstring) can be renamed. A
    // region's name is its real name, and an orphaned bucket has no record
    // entry left to write one onto.
    //
    // SNOW-658: what that gates is now the whole inline-edit affordance,
    // not just a button — the pencil, the row's own `data-row-renameable`
    // marker (which is what static/js/inline_rename.js recognises) and the
    // hidden editor. The template renders all three unconditionally,
    // because one <template> serves both kinds of row; a row that cannot
    // be renamed sheds them here.
    //
    // The label is no longer in that list. It used to carry a
    // `cursor-text` + hover-border pair stripped on this branch, because
    // clicking it opened the editor; Hugo's "we have inline editing & the
    // pencil - choose one" left the pencil as the only trigger, so the
    // label now looks and behaves the same on every row of every panel and
    // there is nothing here to take off it.
    const renameBtn = fragment.querySelector('[data-downloads-rename]');
    const editor = fragment.querySelector('[data-row-rename-input]');
    const item = fragment.querySelector('li');
    if (row.renameable) {
      if (item) item.setAttribute('data-row-renameable', '');
      if (renameBtn) {
        renameBtn.setAttribute('data-downloads-rename', row.id);
        renameBtn.setAttribute(
          'aria-label',
          interpolate(STRINGS['rename-row-label'], { name: row.label }),
        );
      }
      if (editor) editor.value = row.label;
    } else {
      if (renameBtn) renameBtn.remove();
      if (editor) editor.remove();
    }
    return fragment;
  }

  /**
   * Open the sheet.
   *
   * @returns {Promise<void>}
   */
  async function open() {
    // SNOW-656: opening the sheet no longer turns the overlay on.
    //
    // SNOW-645 called `show()` unconditionally here, on the reasoning that a
    // user who opens "Manage downloads" wants to see what they have, and an
    // overlay nobody knows about is an overlay nobody uses. That was a cheap
    // side effect while the squares were the only thing it changed.
    //
    // It stopped being cheap when the squares and the danger choropleth
    // became mutually exclusive: merely OPENING this sheet took the
    // choropleth off the map, which nobody asked for. The exclusivity has
    // since gone (the squares are a hatch the choropleth reads through), but
    // the auto-show has not come back — an implicit repaint of the map is
    // still a poor trade for discoverability.
    //
    // So the overlay is now entirely the switch's business: it starts at
    // whatever the user last set (the preference is persisted, like the
    // other three panels'), and it stays there until they change it —
    // closing the sheet does not hide it, and opening the sheet does not
    // show it. See the toggle's own change handler, and
    // window.pwaDownloadedOverlay's comment in map.js for why visibility is
    // bound to the switch rather than to the sheet's lifecycle.
    //
    // render() reads window.pwaDownloadedOverlay.isEnabled() to paint the
    // switch, so it now shows the overlay's real state on every open rather
    // than a state this function just imposed.
    // SNOW-658: only one overlay is open over the map at a time. Announced
    // before this sheet is unhidden, so whatever it replaces — the layers
    // menu, one of the two other panels, an anchored detail popup — is gone
    // by the time this one is on screen. It replaces the single hand-wired
    // ``window.pwaLayersMenu?.close()`` this function used to make, which
    // covered the one sibling it happened to know about.
    window.pwaMapOverlays?.opening(SHEET_ID);
    await render();
    sheet.hidden = false;
    // SNOW-658: inset the sheet past the scrubber and the roundel column on
    // desktop — the same call MapSheet.attach()'s own open() makes for the
    // other two UGC sheets. This module does not go through that controller
    // (it owns `sheet.hidden` itself), so it makes the call directly.
    window.pwaOverlayBounds?.positionSheet(sheet);
    sheet.focus();
  }

  /**
   * Close the sheet.
   *
   * The half of the registry's contract this module has to state itself:
   * every other overlay reaches it through
   * ``window.pwaMapOverlays``. Hiding directly, matching ``open()``'s own
   * ``sheet.hidden = false`` — the body is re-cloned on the next open, so
   * there is no teardown to run.
   *
   * @returns {void}
   */
  function close() {
    sheet.hidden = true;
  }

  /**
   * Open the sheet, or close it when it is already open (SNOW-658).
   *
   * What ``#map-custom-download-control`` calls. A roundel that opens a
   * surface closes it on the second tap — the layers pill has always
   * behaved that way, and Hugo's report is that the three panels should
   * behave alike.
   *
   * @returns {Promise<void>}
   */
  async function toggle() {
    if (!sheet.hidden) {
      close();
      return;
    }
    await open();
  }

  // Delegated on the sheet: cloned along with the body template on every
  // render, so a per-element listener would have to be rebound each time.
  //
  // SNOW-634: "Download a custom area" — offline refuses with a toast (no
  // read/write of the area list needs a connection, but STARTING a new
  // download does, mirroring #map-frame-overlay's own Download button).
  // SNOW-637 disables the trigger outright while offline (see render()), so
  // this branch is now the race guard rather than the primary refusal: it
  // covers a connection lost between the paint and the tap, which no
  // render-time check can catch. Online hides this sheet and hands off to
  // map.js's own bridge, which
  // opens framing exactly as the roundel's own click used to. Hiding
  // directly (rather than a dismiss idiom) mirrors `open()`'s own
  // `sheet.hidden = false` above; the sheet does not reopen when framing
  // closes — SNOW-632 already ends a run on an in-overlay "23.4 MB
  // downloaded" + Close, so there is nothing here for a re-open to add.
  sheet.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    const addButton = target.closest('[data-panel-add]');
    if (addButton) {
      // SNOW-749: the account gate, before the connection one. Both are
      // reasons a download cannot start; this is the one the visitor can
      // do something about, and doing it needs the connection anyway.
      if (NEEDS_SIGNIN) {
        window.MapSheet.toast(STRINGS['add-signin']);
        goToSignIn();
        return;
      }
      // SNOW-748: the mode, not the radio — a forced offline mode keeps
      // `navigator.onLine` true, and a download is network use.
      if (!networkInUse()) {
        window.MapSheet.toast(STRINGS['add-offline']);
        return;
      }
      sheet.hidden = true;
      // SNOW-645 review: hiding the sheet here does NOT hide the
      // downloaded-tiles overlay — only the in-sheet toggle's own change
      // handler ever calls hide() now (see this module's header and
      // open()'s own comment for why closing no longer implies off).
      window.pwaCustomAreaDownload?.openFraming();
      return;
    }
    // SNOW-811: the row's name frames its area. First of the row tests,
    // matching the order the other three panels use — the label is the
    // widest target on the row, so a press that IS a focus must be claimed
    // before the action hooks are offered it.
    //
    // The overlay handed over is this panel's own, so a press switches the
    // downloaded-areas squares on through the same bridge the "Display on
    // the map" switch uses (persisting the preference), and `close` is
    // this module's own: below `sm` the sheet covers the map, so a flight
    // behind it would be invisible on the device most likely to be holding
    // the panel open.
    if (
      window.pwaRowFocus?.handleClick(event, {
        overlay: window.pwaDownloadedOverlay,
        close: close,
      })
    ) {
      return;
    }
    if (_handleDownloadHereClick(event)) return;
    if (_handleRenameClick(event)) return;
    _handleDeleteClick(event);
  });

  /**
   * SNOW-749: "Download here" — fetch an account-only area onto this
   * device.
   *
   * Hands off to whichever start control owns the kind of area it is,
   * rather than reimplementing a run: a region goes to
   * ``window.pwaRegionDownload.start`` (map_region_download.js), a custom
   * area to ``window.pwaCustomAreaDownload.openFramingAt``
   * (map_custom_download.js). Both are the same code path the roundel and
   * the add-trigger already use, so the pre-flight, the budget check, the
   * eviction confirm and the recording cannot drift from them.
   *
   * The sheet hides on the way out, mirroring the add-trigger: framing
   * needs the map, and a region run paints its progress on the ribbon
   * roundel behind this sheet.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a "Download here".
   */
  function _handleDownloadHereClick(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return false;
    const button = target.closest('[data-downloads-here]');
    if (!button) return false;

    const areaId = button.getAttribute('data-downloads-here');
    if (!areaId) return true;

    // Offline-integrity, same as the add-trigger: listing costs nothing,
    // starting a download needs a connection.
    //
    // SNOW-748: the mode, not the radio. A forced offline mode leaves
    // ``navigator.onLine`` true, and starting a download there would be
    // the app contradicting the switch the user just set.
    if (!networkInUse()) {
      window.MapSheet?.toast(STRINGS['add-offline']);
      return true;
    }

    const regionId = button.getAttribute('data-downloads-region') || '';
    let bbox = null;
    try {
      const raw = button.getAttribute('data-downloads-bbox');
      bbox = raw ? JSON.parse(raw) : null;
    } catch (_err) {
      bbox = null;
    }

    sheet.hidden = true;
    if (regionId && window.pwaRegionDownload) {
      window.pwaRegionDownload.start(regionId).then(function (started) {
        // A refusal is silent on the roundel — it settles into whatever
        // state it settled into — so say so here rather than leaving the
        // tap unanswered.
        if (!started) window.MapSheet?.toast(STRINGS['download-here-failed']);
      });
      return true;
    }
    if (Array.isArray(bbox) && window.pwaCustomAreaDownload?.openFramingAt) {
      window.pwaCustomAreaDownload.openFramingAt(bbox);
      return true;
    }
    window.MapSheet?.toast(STRINGS['download-here-failed']);
    return true;
  }

  /**
   * The rename half of the sheet's delegated click handler (SNOW-635;
   * rebuilt on Hugo's design by SNOW-658), factored out so the listener
   * above can dispatch to it after ruling out the add-trigger.
   *
   * The row's pencil opens its own inline editor.
   * window.pwaInlineRename owns that interaction — shared with the
   * favourites panel — and calls back once with a name worth writing:
   * Escape, an unchanged name and an emptied field never reach here.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a rename — tells the caller
   *   not to also try the delete handler.
   */
  function _handleRenameClick(event) {
    const row = window.pwaInlineRename?.rowFor(event.target);
    if (!row) return false;

    const button = row.querySelector('[data-downloads-rename]');
    const areaId = button ? button.getAttribute('data-downloads-rename') : '';
    if (!areaId || !window.pwaBasemapDownloads) return true;

    window.pwaInlineRename.begin(row, function (name) {
      window.pwaBasemapDownloads.rename(areaId, name).then(function (ok) {
        if (ok) {
          render();
          return;
        }
        // The name did not land, so it is not shown: the editor has
        // already closed and the label still reads what the record says.
        // A toast rather than text written onto the failing control — the
        // shape delete uses — because that control is now a glyph, and
        // there is nowhere on it for a sentence to go.
        window.MapSheet?.toast(STRINGS['rename-failed']);
      });
    });
    return true;
  }

  /**
   * The delete-button half of the sheet's delegated click handler,
   * factored out so the listener above can dispatch to it after ruling
   * out the add-trigger and the rename button.
   *
   * @param {MouseEvent} event
   * @returns {void}
   */
  function _handleDeleteClick(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    // SNOW-832: one destructive verb again. SNOW-749 ran a second one
    // through this same handler ("Free up space" — local eviction, keep
    // the account row); see includes/_map_downloads_row_actions.html for
    // why it went. What is left is the shape this function had before
    // that ticket, plus the account half of the removal.
    const button = target.closest('[data-downloads-delete]');
    if (!button) return;

    const areaId = button.getAttribute('data-downloads-delete');
    if (!areaId) return;

    const name = button.getAttribute('data-downloads-label') || areaId;
    const size = button.getAttribute('data-downloads-size') || '';
    const onDevice = button.getAttribute('data-downloads-on-device') !== 'false';
    const synced = button.getAttribute('data-downloads-synced') === 'true';
    // Two confirmations, because there are two outcomes and the user is
    // choosing between them:
    //   remove (synced) — gone from here AND from the other devices;
    //   remove (not synced) — the pre-SNOW-749 wording, correct for an
    //     area with no account row behind it.
    //
    // That second branch is keyed off THIS ROW's `synced`, not off
    // `pwaDownloadsSync.isEnabled()`. The global answers "is the feature
    // switched on for this visitor", which is a different question and
    // gives the wrong answer for the rows that matter most: an area
    // downloaded before the flag opened, or one whose queued push has not
    // drained, is unsynced on a page where the feature is fully on. Told
    // "this removes it from your other devices too", the user would be
    // reading a sentence about something that was never anywhere else.
    // The queued `forget()` is still harmless for such a row (404, which
    // the client treats as success — the row is gone either way), so the
    // defect was purely in what we said, which is the half a user acts on.
    const message = synced
      ? interpolate(STRINGS['confirm-forget'], { name: name, size: size })
      : interpolate(STRINGS['confirm-remove'], { name: name, size: size });
    // window.confirm, matching pwa_reset.js's destructive-action idiom —
    // the copy names what goes and what it frees, so the choice can be
    // judged before it is made.
    if (typeof window.confirm === 'function' && !window.confirm(message)) {
      return;
    }

    forgetArea(areaId, onDevice).then(function (ok) {
      if (ok) {
        render();
        return;
      }
      // Nothing was removed, so the row must stay. Say so on the button
      // itself rather than in a toast: it is the control that failed, and
      // the next render (any re-open) clears it.
      button.textContent = STRINGS['remove-failed'] || '';
      button.setAttribute('disabled', '');
    });
  }

  // The budget is written on change, not behind a Save — there is nothing
  // to batch, and a setting that needs confirming reads as riskier than it
  // is. Lowering it below current usage is allowed: refusing would leave a
  // user on a full device unable to say how much room they want to give up.
  sheet.addEventListener('change', function (event) {
    const target = /** @type {HTMLSelectElement} */ (event.target);
    if (!target || !target.matches || !target.matches('[data-downloads-budget]')) {
      return;
    }
    const core = manageCore();
    if (!core) return;
    const mb = core.clampBudgetMb(target.value);
    writeMeta(BUDGET_KEY, mb).then(function () {
      // Re-render for the running total and the over-budget line, both of
      // which are stated against the budget that just changed.
      render();
    });
  });

  // SNOW-645 review: the "Available offline" toggle drives the overlay
  // DIRECTLY — show()/hide() are the only writers of
  // window.pwaDownloadedOverlay's visibility, so this is the single place
  // outside open() that ever calls them, and it never touches a flag of
  // its own (render() reads isVisible() back, above). No re-render needed:
  // nothing else on the sheet depends on this state.
  sheet.addEventListener('change', function (event) {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!target || !target.matches || !target.matches('#map-downloads-overlay-toggle')) {
      return;
    }
    if (target.checked) {
      window.pwaDownloadedOverlay?.show();
    } else {
      window.pwaDownloadedOverlay?.hide();
    }
  });

  // The in-sheet switch is not the only thing that can turn the overlay off
  // — placement focus clears every app layer off the map — and a sheet still
  // open behind that has to show it, rather than sitting on a checked switch
  // for an overlay that is no longer drawn. (The Bulletins step control was
  // the other such writer until the two layers stopped being exclusive.)
  //
  // Sets the checkbox rather than re-rendering: render() re-clones the whole
  // body template and re-reads IndexedDB, and nothing else on the sheet
  // depends on this state (its own change handler already skips the
  // re-render for the same reason). Not skipped while hidden either — this
  // is one attribute write on an element that stays in the DOM, and leaving
  // it stale would only be corrected by render()'s read-back on the next
  // open, which is exactly the drift this closes.
  document.addEventListener('snowdesk:downloaded-overlay-changed', function (event) {
    const overlayToggle = /** @type {HTMLInputElement|null} */ (
      sheet.querySelector('#map-downloads-overlay-toggle')
    );
    if (overlayToggle) overlayToggle.checked = !!(event.detail && event.detail.visible);
  });

  // SNOW-637: a sheet left open when the connection drops has to reflect it
  // there and then — waiting for the next open would leave a live-looking
  // trigger on screen for as long as the user keeps the sheet up. Same
  // broadcast, same reaction as the two surfaces that already do this:
  // map_layer_sync_status.js's menu dots and map.js's download roundel.
  //
  // Skipped while hidden: open() renders on its own way in, and re-rendering
  // a closed sheet would re-read IndexedDB on every transition for a surface
  // nobody is looking at.
  document.addEventListener('snowdesk:connectivity-changed', function () {
    if (sheet.hidden) return;
    render();
  });

  // SNOW-658: one open map overlay at a time. Registered here rather than
  // through MapSheet.attach (this sheet does not use that controller — it
  // owns `sheet.hidden` itself), but the contract is identical.
  window.pwaMapOverlays?.register(SHEET_ID, {
    isOpen: function () {
      return !sheet.hidden;
    },
    close: close,
  });

  /**
   * SNOW-749: push every local area the account has not been told about.
   *
   * The migration path, and the only one there is. ``push()`` fires from
   * the two places a download is RECORDED, so it covers downloads made
   * from here on and nothing else — while every existing user's areas were
   * downloaded before this ticket, when the product had no account gate at
   * all. Without this call they would sign in and their areas would never
   * reach the account, never appear on a second device, and never be
   * retried, because nothing else ever looks at them again.
   *
   * Runs for any signed-in visitor. Fire-and-forget: it is a background
   * reconciliation, nothing on screen waits for it, and a failure costs
   * one more attempt on the next load rather than anything the user can
   * see. Idempotent through
   * ``downloads_sync.js``'s own ``basemap.syncedAreaIds`` marker plus the
   * endpoint's ``update_or_create`` on ``(user, area_id)``, so calling it
   * on every load is a no-op after the first.
   *
   * Deferred to DOMContentLoaded rather than run at parse time, and the
   * reason is load order: this module is loaded from the sheet's own
   * partial in the page CONTENT, while ``window.pwaBasemapDownloads`` — the
   * bridge ``adopt()`` reads the local areas through — is published by
   * ``map_basemap_downloads.js`` in ``extra_js``. Deferred classic scripts
   * run in document order, so at this file's parse time that bridge does
   * not exist yet and ``adopt()`` would silently find nothing and mark
   * nothing, which is exactly the permanent no-op this call exists to
   * prevent.
   *
   * @returns {void}
   */
  function _adoptLocalAreas() {
    // Guarded here as well as inside `adopt()` itself. The duplication is
    // deliberate: this states the precondition at the CALL site, where a
    // reader asks "when does this run", rather than leaving the answer two
    // modules away.
    if (!DOWNLOADS_ELIGIBLE) return;
    window.pwaDownloadsSync?.adopt();
  }

  // Only ``'complete'`` runs it now; every other state waits for
  // DOMContentLoaded.
  //
  // NOT the ``readyState === 'loading'`` idiom used elsewhere in this
  // codebase (``mutation_queue.js``'s ``_wireLifecycle``), which would be
  // wrong here and silently so. The parser sets ``readyState`` to
  // ``'interactive'`` BEFORE it runs deferred scripts, not after — so
  // during this file's own execution the state is ``'interactive'``, that
  // idiom takes its else branch, and the call fires immediately: before
  // ``map_basemap_downloads.js`` (a later deferred script) has published
  // the bridge ``adopt()`` needs. It would find no areas, mark nothing and
  // never run again, which is precisely the permanent no-op above.
  // DOMContentLoaded is the first moment every deferred script has run.
  if (document.readyState === 'complete') {
    _adoptLocalAreas();
  } else {
    document.addEventListener('DOMContentLoaded', _adoptLocalAreas);
  }

  window.pwaDownloadsManager = Object.freeze({
    open: open,
    toggle: toggle,
    refresh: render,
  });
})();
