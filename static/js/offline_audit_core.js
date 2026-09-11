// @ts-check
/*
 * static/js/offline_audit_core.js — answer "what will work offline?" from
 * one set of raw storage readings (SNOW-907).
 *
 * ## The question, and the shape that follows from it
 *
 * The readout is a FIXED list of capabilities, each answered Yes or No.
 * Not an inventory: how many program files are cached, how many reports
 * are stored, how many saved places there are — none of that is a
 * question anyone has. "Will the bulletins I have opened still open?" is.
 *
 * So ``ROWS`` below is a constant, declared in full before anything is
 * read, and every run resolves exactly those rows in exactly that order.
 * A device with nothing stored produces the same thirteen rows as a
 * device with everything, all reading No. That is what makes the table
 * scannable, comparable between runs, and paintable before the first
 * reading has landed — the panel draws the whole list as waiting, then
 * fills each answer in.
 *
 * Several rows are answered from more than one reading. "The app opens"
 * is the map page's HTML being in the shell cache AND its
 * ``X-SW-Principal`` stamp matching the account signed in now — the two
 * halves of the failure that produced this ticket. The user does not care
 * which half; the summary tells them, the row does not.
 *
 * ## Why the principal half matters
 *
 * A region downloaded before a journey is irrelevant if the map page's
 * own HTML was never cached, or was cached under a different account:
 * ``sw.js``'s ``_networkFirstFallback`` refuses an entry whose stamp does
 * not match and falls through to ``offline.html`` without saying so
 * anywhere. Nothing on the device reported either condition before this
 * module.
 *
 * ## The log and the summary do different jobs
 *
 * **The log is evidence.** One line per capability, and strictly one:
 * label, answer. Its job is to show that thirteen separate things were
 * looked at, which is what makes the conclusion believable — nobody
 * trusts a single green tick from the app that just failed them.
 *
 * **The summary is the answer.** A verdict sentence plus one paragraph of
 * ordinary prose saying what to expect and what fixes it. It is where
 * detail a row cannot hold goes: which area is incomplete, which account
 * the saved page belongs to.
 *
 * A row NEVER carries its own explanation. Per-row helper text made the
 * panel three times taller, turned scanning into reading, and printed one
 * shared remedy once per row — ``composeSummary`` says it once instead.
 *
 * ## Two rules the whole report follows
 *
 * **A reading that could not be taken is ``unknown``, never ``yes``.** The
 * report is read by someone already let down once by a surface that said
 * everything was fine, so the absence of evidence takes its own state and
 * counts towards nothing in either direction.
 *
 * **A No is not always a fault.** A row marked ``critical`` blocks the
 * headline verdict when it answers No — there is no point telling someone
 * their bulletins are saved if the app will not open. Every other No is
 * simply something this device cannot do offline, which is worth knowing
 * and is not an error.
 *
 * Pure: no DOM, no fetch, no storage. Every reading arrives as an
 * argument, collected by ``offline_audit.js``, which is also the only
 * caller.
 */

(function () {
  'use strict';

  /**
   * @typedef {'yes'|'no'|'blocked'|'unknown'|'pending'} AuditStatus
   *   ``blocked`` is a No on a critical row — the same answer, but the
   *   reason the verdict is a failure rather than a limitation.
   */

  /**
   * One line of the log.
   *
   * @typedef {Object} AuditCheck
   * @property {string} id
   * @property {string} label The capability, phrased as the thing the
   *   user wants ("Bulletins you have opened").
   * @property {string} value The answer. Yes / No / — and nothing longer:
   *   the row is one line and detail belongs in the summary.
   * @property {AuditStatus} status
   * @property {string} [reason] An untranslated discriminator, where the
   *   verdict has to tell two failures of the same row apart.
   * @property {string} [group] Which remedy this row's No shares with
   *   others, for ``composeSummary``.
   * @property {string} [effect] What the user will notice, as a verb
   *   phrase completing the group's lead ("will look plain"). It names
   *   its own subject, so the lead needs no separate list of them.
   * @property {string} [note] A standalone summary clause, for a No that
   *   shares its remedy with nothing else.
   */

  /**
   * @typedef {Object} AuditSection
   * @property {string} id
   * @property {string} title
   * @property {AuditCheck[]} checks
   */

  /**
   * @typedef {Object} AuditReport
   * @property {{status: 'ok'|'warn'|'fail'|'pending', text: string}} verdict
   * @property {string} summary The paragraph under the verdict. Empty
   *   when the verdict is the whole truth.
   * @property {AuditSection[]} sections
   * @property {{total: number, yes: number, no: number}} counts
   * @property {string} generatedAt ISO 8601.
   * @property {{timedOut: string[], latched?: boolean}|null} [degraded]
   *   Which readings did not answer, where some did not. Carried for
   *   ``reportText``: on a phone with no devtools, a copied report is the
   *   only place this ever exists.
   * @property {string|null} [failure] Set where the collection threw
   *   rather than returning readings.
   * @property {boolean} pending True for the skeleton painted before any
   *   reading has landed.
   */

  /**
   * @typedef {Object} ShellEntry
   * @property {string} url
   * @property {boolean} [isPage] Whether the entry is page HTML rather
   *   than an asset — decided by the collector, which is the half that
   *   can afford to look.
   * @property {string|null} [principal] The ``X-SW-Principal`` stamp, on
   *   pages only. Null where there is none, which is never servable.
   */

  /**
   * @typedef {Object} AreaReading
   * @property {string} id
   * @property {'region'|'custom'|'base'} [kind]
   * @property {string} [type] 'dropzone' on a drop-zone download — see
   *   ``areaKind``.
   * @property {string} [name]
   * @property {string|null} [basemapKey]
   * @property {number} [bytes]
   * @property {string[]} [deps]
   * @property {boolean} [bucketPresent]
   * @property {boolean} [bucketReadable] False where the bucket read did
   *   not answer. Distinct from ``bucketPresent: false``, which is a
   *   bucket that WAS read and is not there — the difference between "this
   *   download is gone" and "we could not look", and the report may only
   *   say the first of those.
   * @property {string[]} [entries]
   */

  /**
   * Everything ``offline_audit.js`` managed to read. Every field is
   * optional: a device broken enough to fail these reads is exactly the
   * device whose user is reading this.
   *
   * @typedef {Object} AuditReadings
   * @property {string} [now]
   * @property {boolean} [online]
   * @property {{supported?: boolean, registered?: boolean,
   *   controlled?: boolean}} [serviceWorker]
   * @property {{usage?: number, quota?: number, persisted?: boolean}|null}
   *   [storage]
   * @property {ShellEntry[]} [shellEntries]
   * @property {string|null} [currentPrincipal]
   * @property {string} [mapPath]
   * @property {string[]|null} [mapDependencies] SNOW-912: the same-origin
   *   modules the cached map page's HTML boots from. Null when there is no
   *   page to read, or its body could not be read — answered No, not Yes.
   * @property {string|null} [mapDay] SNOW-914: the day the cached map page
   *   will open on — its own ``data-today``, which is the date its boot
   *   fetch puts in the ratings URL. Null where no page carries one.
   * @property {string|null} [selectedBasemap] SNOW-913: the basemap key the
   *   reader is looking at — their stored choice, or the deployed default
   *   where a server could say. Null only where neither is knowable
   *   (static/offline.html, nothing chosen), in which case no row claims to
   *   be the current one.
   * @property {AreaReading[]} [areas]
   * @property {Record<string, number|null>} [stores] Row counts by store.
   * @property {Record<string, {features: number|null,
   *   principal?: string|null}>} [overlays] SNOW-914: each
   *   ``data:map_overlays`` row — how many features it holds, and the
   *   principal it was stamped with. Presence alone is not an answer: a row
   *   stamped for another account is refused by the reader, and an empty
   *   FeatureCollection draws nothing.
   * @property {string[]} [panelKeys] Which ``data:panel_rows`` rows exist.
   * @property {{count?: number|null}} [mutations]
   * @property {boolean} [dbAvailable]
   * @property {boolean} [cachesReadable] False where Cache Storage could
   *   not be LISTED. Every row read out of it then answers unknown: an
   *   empty ``shellEntries`` is indistinguishable from an unread one, and
   *   the No it would otherwise produce is this panel's loudest claim.
   * @property {boolean} [shellPartial] True where the listing came back
   *   but opening a page in it did not — a narrower failure costing only
   *   the rows that need a page's stamp or its body.
   * @property {{timedOut: string[], latched?: boolean}|null} [degraded]
   *   Which readings overran their budget. See ``bounded`` in
   *   offline_audit.js and
   *   docs/decisions/bounded-offline-read-paths.md.
   * @property {string|null} [failure] Set where the collection threw. Every
   *   row then answers unknown — a check that did not run diagnoses
   *   nothing.
   */

  // The fixed list. Its order is the order it paints, and it is the order
  // the questions depend on each other: nothing below "The app opens" is
  // reachable if that answers No.
  //
  // `critical` marks a row whose No means the app is not usable offline at
  // all, as opposed to one capability being unavailable. Only four rows
  // are: everything else is a limitation worth knowing, not a fault.
  var ROWS = [
    { id: 'offline-mode', section: 'access', critical: true },
    { id: 'app-opens', section: 'access', critical: true },
    { id: 'app-looks-right', section: 'access', critical: false },
    { id: 'danger-ratings', section: 'map', critical: false },
    { id: 'region-shapes', section: 'map', critical: false },
    { id: 'bulletins', section: 'content', critical: false },
    { id: 'saved-places', section: 'content', critical: false },
    { id: 'routes', section: 'content', critical: false },
    { id: 'reports', section: 'content', critical: false },
    { id: 'weather', section: 'content', critical: false },
  ];

  // The download rows are NOT fixed, and they are the one place that
  // exception is right: "will the map draw" is not a question about the
  // map, it is a question about the particular ground this user chose to
  // take with them. One row that says Yes because SOME area draws is no
  // use to someone whose Verbier download is the broken one — so every
  // download gets its own named row.
  //
  // All of them under ONE heading, whatever kind they are. Three headings
  // for three kinds put an empty-looking section between every pair of
  // rows on a device with one of each, and the kind is a property of the
  // row rather than a place to file it — so it rides along in the label
  // instead, where a drop zone reads as one.
  //
  // Ordered regions first, because a region is what most people download
  // and the ones they drew themselves are the exceptions.
  var DOWNLOAD_KIND_ORDER = ['region', 'dropzone', 'custom'];

  var SECTIONS = [
    { id: 'access', key: 'section-access' },
    { id: 'map', key: 'section-map' },
    { id: 'downloads', key: 'section-downloads' },
    { id: 'content', key: 'section-content' },
  ];

  /**
   * A human byte figure, in the units the rest of the app uses.
   *
   * Zero is treated as absent, not as a measurement. A download record
   * whose byte figure has not landed carries ``bytes: 0`` (SNOW-863) —
   * the tiles are on the device either way — and "0 MB" beside an area
   * that demonstrably holds four hundred tiles is a worse answer than
   * declining to give one.
   *
   * @param {*} bytes
   * @returns {string} ``'—'`` for zero, a negative, or a non-number.
   */
  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return '—';
    var n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return '—';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
    var mb = n / (1024 * 1024);
    return (mb < 10 ? mb.toFixed(1) : Math.round(mb)) + ' MB';
  }

  /**
   * Which of ``wanted`` is absent from ``present``.
   *
   * Deliberately the same contract as
   * ``pwaBasemapDownloadCore.missingRenderDependencies`` — subset in the
   * order given, deduplicated, and ``[]`` for an empty ``wanted``, which
   * means "nothing was claimed" and must be read as UNKNOWN rather than
   * as a pass (the three-row resolution rule in
   * docs/decisions/a-downloaded-area-is-verified-by-what-it-renders.md).
   *
   * Restated here rather than imported. That module is 116 KB of tile
   * arithmetic this report has no use for, and it would have to be
   * precached for ``offline.html`` to run the same check — which is the
   * one surface where this check matters most.
   *
   * @param {string[]} wanted
   * @param {Set<string>|string[]} present
   * @returns {string[]}
   */
  function missingFrom(wanted, present) {
    if (!Array.isArray(wanted) || wanted.length === 0) return [];
    var have = present instanceof Set ? present : new Set(present || []);
    var missing = [];
    var seen = new Set();
    for (var i = 0; i < wanted.length; i += 1) {
      var url = wanted[i];
      if (typeof url !== 'string' || !url) continue;
      if (seen.has(url)) continue;
      seen.add(url);
      if (!have.has(url)) missing.push(url);
    }
    return missing;
  }

  /**
   * Which kind of shell-cache entry a URL is.
   *
   * Classified by path rather than by the response's content type: the
   * reading is taken from ``cache.keys()``, which returns Requests, and
   * asking each one for its Response would turn a cheap listing into one
   * round trip per entry on a device that may hold hundreds.
   *
   * @param {string} url
   * @returns {'page'|'script'|'style'|'font'|'image'|'feed'|'other'}
   */
  function classifyEntry(url) {
    var path = pathOf(url);
    if (path.startsWith('/api/')) return 'feed';
    if (/\.m?js$/.test(path)) return 'script';
    if (/\.css$/.test(path)) return 'style';
    if (/\.(woff2?|ttf|otf)$/.test(path)) return 'font';
    if (/\.(png|jpe?g|svg|webp|ico|avif)$/.test(path)) return 'image';
    if (/\.[a-z0-9]{2,5}$/i.test(path)) return 'other';
    return 'page';
  }

  /**
   * The same-origin scripts and stylesheets a cached page's HTML asks for
   * (SNOW-912).
   *
   * This is what makes "The app opens" an answer about the page rather
   * than about the cache in general. The row used to pass on
   * ``fileCounts(r).script > 0`` — is there ANY JavaScript here — which is
   * true on every device with a worker, because ``AUDIT_SCRIPTS``
   * precaches two scripts on install. A page saved without the modules it
   * boots from would have read Yes and opened to a blank frame, and a row
   * that says Yes to someone looking at a blank frame costs more than the
   * row is worth.
   *
   * The same rule the download rows already answer to:
   * docs/decisions/a-downloaded-area-is-verified-by-what-it-renders.md. An
   * empty list means the page named nothing, which ``missingFrom`` treats
   * as unknown rather than as a pass.
   *
   * Matched on the URL's extension rather than on the tag, for the reason
   * ``sw.js``'s ``_shellSubresources`` is — a regex over HTML cannot pair
   * an attribute with its element reliably and does not need to. The two
   * implementations are held together by a shared fixture table in
   * tests/js/test_sw.js; they cannot be one function, because the worker
   * is a classic script that would have to importScripts this whole module
   * to reach it.
   *
   * @param {string} html
   * @param {string} origin The page's own origin — same-origin is what the
   *   shell cache holds, and a cross-origin CDN entry is somebody else's
   *   cache policy.
   * @returns {string[]} Absolute URLs, deduplicated, in document order.
   */
  function pageDependencies(html, origin) {
    if (typeof html !== 'string' || !html) return [];
    var urls = /** @type {string[]} */ ([]);
    var seen = new Set();
    var pattern = /(?:src|href)=["']([^"'\s]+\.(?:js|css))(?:\?[^"']*)?["']/gi;
    var match = pattern.exec(html);
    while (match) {
      var absolute = /** @type {string|null} */ (null);
      try {
        var url = new URL(match[1], origin);
        if (url.origin === new URL(origin).origin) absolute = url.toString();
      } catch (_err) {
        absolute = null;
      }
      if (absolute && !seen.has(absolute)) {
        seen.add(absolute);
        urls.push(absolute);
      }
      match = pattern.exec(html);
    }
    return urls;
  }

  /**
   * The basemap catalogue a cached map page carries (SNOW-913).
   *
   * Both halves of what ``map.js`` resolves the visitor's choice against:
   * every ``data-basemap-key`` the picker renders, and the deployed default
   * on ``#map``'s ``data-default-basemap-key``.
   *
   * Read out of the page the device actually holds, for the reason
   * ``pageDay`` is: that page is the one that will boot, and its catalogue
   * is the one the resolution will run against — not whatever the server
   * is serving now.
   *
   * @param {string} html
   * @returns {{keys: string[], fallback: string|null}}
   */
  function pageBasemaps(html) {
    var keys = /** @type {string[]} */ ([]);
    if (typeof html !== 'string' || !html) return { keys: keys, fallback: null };
    var pattern = /data-basemap-key=["']([A-Za-z0-9_-]+)["']/gi;
    var match = pattern.exec(html);
    while (match) {
      if (keys.indexOf(match[1]) === -1) keys.push(match[1]);
      match = pattern.exec(html);
    }
    var fallback = /data-default-basemap-key=["']([A-Za-z0-9_-]+)["']/i.exec(html);
    return { keys: keys, fallback: fallback ? fallback[1] : null };
  }

  /**
   * The basemap the reader is looking at, resolved the way the map resolves
   * it (SNOW-913).
   *
   * ``map.js``:
   *
   *     const preferred = (stored && BASEMAP_OPTIONS[stored])
   *       ? stored
   *       : DEFAULT_BASEMAP_KEY;
   *
   * A stored key the catalogue no longer offers is NOT what the map will
   * show — a style removed from the picker leaves the preference behind in
   * ``localStorage``, and the map quietly falls back to the deployed
   * default. Naming the stale key would be this report doing exactly what
   * this ticket exists to stop: labelling a basemap "on screen" that is
   * not.
   *
   * With no cached page there is no catalogue to check against, and the
   * stored key is the best available answer — the page being absent is
   * already the blocking row above.
   *
   * @param {{keys: string[], fallback: string|null}|null} catalogue
   * @param {string|null} stored The visitor's ``localStorage`` choice.
   * @param {string|null} serverDefault The host page's own
   *   ``data-default-basemap-key``, where a server rendered one.
   * @returns {string|null}
   */
  function resolveBasemap(catalogue, stored, serverDefault) {
    var keys = catalogue && Array.isArray(catalogue.keys) ? catalogue.keys : [];
    if (keys.length > 0) {
      if (stored && keys.indexOf(stored) >= 0) return stored;
      return (catalogue && catalogue.fallback) || serverDefault || null;
    }
    return stored || serverDefault || null;
  }

  /**
   * The day a cached map page will open on (SNOW-914).
   *
   * ``#season-scrubber``'s ``data-today``, read out of the HTML the device
   * actually holds. That is deliberately NOT the device's clock: the map
   * reads the same attribute (``readTodayDateParam`` in map_shared.js), so
   * a page cached on Tuesday boots on Tuesday's date however long it sits
   * there, and the feed it asks for is Tuesday's.
   *
   * This is what makes "Danger ratings" answerable at all. The row used to
   * prefix-match ``/api/ratings/`` and say Yes for ANY day's cached feed,
   * while the boot fetch asks for one specific day and
   * ``_staleWhileRevalidate`` matches exact URLs — so a device holding last
   * week's ratings opened to a blank choropleth under a green row.
   *
   * @param {string} html
   * @returns {string|null} ``YYYY-MM-DD``, or null where the page carries
   *   no readable attribute — answered unknown rather than guessed at.
   */
  function pageDay(html) {
    if (typeof html !== 'string' || !html) return null;
    var match = /id=["']season-scrubber["'][^>]*?data-today=["'](\d{4}-\d{2}-\d{2})["']/i.exec(
      html,
    );
    if (match) return match[1];
    // Attribute order is not guaranteed — djangofmt may put `data-today`
    // ahead of `id`, and a future template edit certainly may.
    match = /data-today=["'](\d{4}-\d{2}-\d{2})["'][^>]*?id=["']season-scrubber["']/i.exec(
      html,
    );
    return match ? match[1] : null;
  }

  /**
   * The path part of a URL, query dropped.
   *
   * @param {string} url
   * @returns {string}
   */
  function pathOf(url) {
    try {
      return new URL(url, 'https://snowdesk.info').pathname;
    } catch (_err) {
      return String(url || '');
    }
  }

  /**
   * Look up one string, falling back to its key so a missing entry is
   * visible rather than ``undefined``.
   *
   * @param {Record<string, string>} strings
   * @param {string} key
   * @returns {string}
   */
  function s(strings, key) {
    var value = strings && strings[key];
    return typeof value === 'string' && value ? value : key;
  }

  /**
   * Substitute ``%(name)s`` placeholders, by name.
   *
   * By name and never positionally, for the reason ``i18n_strings.js``
   * gives at length: a locale is free to reorder Django's placeholders.
   *
   * @param {string} template
   * @param {Record<string, string|number>} values
   * @returns {string}
   */
  function fill(template, values) {
    return String(template).replace(/%\(([a-z_]+)\)s/g, function (whole, name) {
      return Object.prototype.hasOwnProperty.call(values, name)
        ? String(values[name])
        : whole;
    });
  }

  /**
   * Join a list into readable prose — "a", "a and b", "a, b and c".
   *
   * Both joins are TEMPLATES rather than a separator string, and that is
   * not over-engineering: ``pwaStrings.read`` collapses and trims the
   * whitespace of every value it reads back, so a separator of ``", "``
   * arrives as ``","`` and the list runs together with no spaces. A space
   * in the middle of a template survives, because it is not at either
   * end. (A locale is also free to reorder or drop the comma, which a
   * bare separator could not express either.)
   *
   * @param {string[]} items
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function joinList(items, t) {
    var list = (items || []).filter(Boolean);
    if (list.length === 0) return '';
    if (list.length === 1) return list[0];
    var last = list[list.length - 1];
    var rest = list.slice(0, -1);
    var joined = rest.reduce(function (acc, item) {
      return acc ? fill(s(t, 'list-join'), { first: acc, rest: item }) : item;
    }, '');
    return fill(s(t, 'list-pair'), { first: joined, last: last });
  }

  /**
   * "it" / "both" / "all of them" — the object of a remedy sentence that
   * fixes more than one thing at once.
   *
   * The many case carries no digit on purpose: "fixes all 3 of them" puts
   * a numeral in the middle of a sentence of prose, and the number is not
   * the point — the reader can see how many rows said No.
   *
   * @param {number} count
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function quantify(count, t) {
    if (count <= 1) return s(t, 'count-one');
    if (count === 2) return s(t, 'count-two');
    return fill(s(t, 'count-many'), { n: count });
  }

  /**
   * Whether a cached page's stamp lets it be served to the principal
   * signed in now.
   *
   * Mirrors ``sw.js``'s ``_principalMatches``, including its fail-closed
   * treatment of an absent or ``'unknown'`` stamp: an entry the worker
   * will never serve must never be reported as saved.
   *
   * @param {string|null|undefined} stamped
   * @param {string|null|undefined} current
   * @returns {boolean}
   */
  function principalMatches(stamped, current) {
    if (!stamped || stamped === 'unknown') return false;
    return stamped === (current || 'anonymous');
  }

  /**
   * A stamp in words. The account uuid is never shown whole — it names an
   * account to anyone reading over a shoulder and means nothing to the
   * person holding the phone; the first segment tells two apart.
   *
   * @param {string|null|undefined} principal
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function describePrincipal(principal, t) {
    if (!principal || principal === 'unknown') return s(t, 'principal-unknown');
    if (principal === 'anonymous') return s(t, 'principal-anonymous');
    return fill(s(t, 'principal-account'), { id: String(principal).slice(0, 8) });
  }

  /**
   * The cached pages, split into those this account can be served and the
   * map page's own entry whatever its stamp.
   *
   * @param {AuditReadings} r
   * @returns {{usable: ShellEntry[], mapEntry: ShellEntry|null}}
   */
  function pages(r) {
    var entries = (Array.isArray(r.shellEntries) ? r.shellEntries : []).filter(
      function (entry) {
        return entry && entry.isPage;
      },
    );
    // Matched on pathname alone: the map writes ``?d=YYYY-MM-DD`` with
    // ``history.replaceState`` while the user scrubs, those URLs are never
    // fetched and never cached, and ``_networkFirstFallback`` does the same
    // ``ignoreSearch`` lookup before giving up.
    var wanted = r.mapPath || '/';
    var mapEntry = /** @type {ShellEntry|null} */ (null);
    entries.forEach(function (entry) {
      if (!mapEntry && pathOf(entry.url) === wanted) mapEntry = entry;
    });
    return {
      usable: entries.filter(function (entry) {
        return principalMatches(entry.principal, r.currentPrincipal);
      }),
      mapEntry: mapEntry,
    };
  }

  /**
   * Every URL the shell cache holds, as a Set.
   *
   * @param {AuditReadings} r
   * @returns {Set<string>}
   */
  /**
   * The dependencies of one kind, by file extension (SNOW-914).
   *
   * ``pageDependencies`` returns one flat list so it can be held to the
   * same answers as ``sw.js``'s ``_shellSubresources`` — the warm fetches
   * both kinds and has no reason to tell them apart. The report does: the
   * scripts decide whether the app opens and the stylesheets decide whether
   * it looks right, which are two rows with two different consequences.
   *
   * @param {string[]} deps
   * @param {'js'|'css'} kind
   * @returns {string[]}
   */
  function dependenciesOfKind(deps, kind) {
    var suffix = '.' + kind;
    return (Array.isArray(deps) ? deps : []).filter(function (url) {
      return pathOf(url).slice(-suffix.length).toLowerCase() === suffix;
    });
  }

  // SNOW-915: the region-id shape Django routes bulletins on —
  // ``RegionIdConverter.regex`` in apps/regions/converters.py, which is
  // deliberately tight enough to reject ``wp-login`` and every other
  // alphabetic probe. Restated rather than shared: there is no way to hand
  // a Python converter's regex to a module that also runs on a static page
  // with no server behind it.
  var REGION_ID = /^[a-z]{2}-(?=[a-z0-9-]*\d)[a-z0-9]+(-[a-z0-9]+)*$/i;

  /**
   * Whether a cached page's path is a bulletin (SNOW-915).
   *
   * Three forms, all served by ``bulletin_detail``: ``/<region_id>/``,
   * ``/<region_id>/<slug>/`` and ``/<region_id>/<slug>/<date>/``. Only the
   * first segment decides it — the region id is the discriminator, and the
   * legal pages registered ahead of those patterns (``/help/``,
   * ``/privacy/``, ``/compare/``) cannot match it.
   *
   * @param {string} path
   * @returns {boolean}
   */
  function isBulletinPath(path) {
    var segments = String(path || '')
      .split('/')
      .filter(Boolean);
    if (segments.length === 0 || segments.length > 3) return false;
    return REGION_ID.test(segments[0]);
  }

  // SNOW-914: the overlays ``map_overlay_offline_cache.js`` stamps with a
  // principal and refuses to read back for anyone else. Restated here for
  // the reason everything in this module is restated — the report must not
  // take the app's word for what the app will manage to read.
  var PRINCIPAL_SCOPED_OVERLAYS = ['favourites', 'routes'];

  /**
   * What one cached overlay will actually give the user (SNOW-914).
   *
   * Three answers, where the row used to have one. ``getOverlay`` returns
   * null for a row whose ``principal`` does not match the account signed in
   * now, so an account-scoped row from another session is on the device and
   * invisible — the row said Yes and the map drew nothing. A row holding an
   * empty FeatureCollection is readable and equally empty; that is not a
   * fault and not a capability, so it is neither Yes nor No.
   *
   * @param {AuditReadings} r
   * @param {string} key
   * @returns {{status: 'yes'|'no'|'empty', reason?: string,
   *   principal?: string|null}}
   */
  function overlayState(r, key) {
    var row = (r.overlays || {})[key];
    if (!row) return { status: 'no', reason: 'absent' };
    if (PRINCIPAL_SCOPED_OVERLAYS.indexOf(key) >= 0) {
      var stamped = row.principal === undefined ? null : row.principal;
      if (stamped !== (r.currentPrincipal || null)) {
        return { status: 'no', reason: 'principal', principal: stamped };
      }
    }
    if (row.features === null || row.features === undefined) {
      return { status: 'no', reason: 'unreadable' };
    }
    return row.features > 0 ? { status: 'yes' } : { status: 'empty' };
  }

  /**
   * One overlay row's answer, with the note that explains a No.
   *
   * @param {{status: string, reason?: string, principal?: string|null}} state
   * @param {Record<string, string>} t
   * @param {string} noteKey Which ``note-no-*`` / ``note-empty-*`` pair
   *   this row uses.
   * @returns {{status: AuditStatus, reason?: string, note?: string}}
   */
  function overlayAnswer(state, t, noteKey) {
    if (state.status === 'yes') return { status: 'yes' };
    if (state.status === 'empty') {
      // Nothing stored because there is nothing to store. Unknown rather
      // than Yes (nothing will appear) or No (nothing is broken), and it
      // counts towards neither side of the tally.
      return { status: 'unknown', reason: 'empty', note: s(t, 'note-empty-' + noteKey) };
    }
    if (state.reason === 'principal') {
      return {
        status: 'no',
        reason: 'principal',
        note: fill(s(t, 'note-other-account'), {
          stamped: describePrincipal(state.principal, t),
        }),
      };
    }
    return { status: 'no', reason: state.reason, note: s(t, 'note-no-' + noteKey) };
  }

  /**
   * Every URL the shell cache holds, as a Set.
   *
   * @param {AuditReadings} r
   * @returns {Set<string>}
   */
  function urlsIn(r) {
    var urls = new Set();
    (Array.isArray(r.shellEntries) ? r.shellEntries : []).forEach(function (entry) {
      if (entry && entry.url) urls.add(entry.url);
    });
    return urls;
  }

  /**
   * How many shell-cache entries of each kind are held.
   *
   * @param {AuditReadings} r
   * @returns {Record<string, number>}
   */
  function fileCounts(r) {
    var counts = { script: 0, style: 0, font: 0, image: 0, feed: 0, other: 0 };
    (Array.isArray(r.shellEntries) ? r.shellEntries : []).forEach(function (entry) {
      if (!entry || entry.isPage) return;
      var kind = classifyEntry(entry.url);
      if (kind === 'page') return;
      counts[kind] = (counts[kind] || 0) + 1;
    });
    return counts;
  }

  // SNOW-914: the two feeds the map's COLD OPEN asks for, exactly as
  // map.js builds them:
  //
  //     fetch(REGIONS_URL + '?country=ch')
  //     fetch(RATINGS_URL + '?d=' + readDisplayDate() + '&country=ch')
  //
  // ``_staleWhileRevalidate`` matches exact URLs — no ``ignoreSearch`` —
  // so any other day's ratings, or any other country's outlines, are a
  // miss and the map paints nothing. The country is hard-coded there and
  // so it is here; a grep for this constant finds both sides the day that
  // changes.
  var BOOT_COUNTRY = 'ch';

  /**
   * Whether the shell cache holds a feed the boot will actually ask for.
   *
   * @param {AuditReadings} r
   * @param {string} path The feed's pathname.
   * @param {Record<string, string>} params Query parameters that must
   *   match. Order-independent, because a URL's parameters are.
   * @returns {boolean}
   */
  function hasBootFeed(r, path, params) {
    return (Array.isArray(r.shellEntries) ? r.shellEntries : []).some(function (entry) {
      if (!entry || !entry.url || pathOf(entry.url) !== path) return false;
      var query = /** @type {URLSearchParams|null} */ (null);
      try {
        query = new URL(entry.url, 'https://snowdesk.info').searchParams;
      } catch (_err) {
        return false;
      }
      if (!query) return false;
      var found = query;
      return Object.keys(params).every(function (key) {
        return found.get(key) === params[key];
      });
    });
  }

  /**
   * The state of one downloaded area, from its record and its bucket.
   *
   * Three questions, in the order a failure is worth knowing about: is the
   * bucket there at all, does it hold anything, and does it hold the four
   * documents MapLibre needs before a single tile is reachable. The third
   * is SNOW-843's whole bug class — a perfect tile set with no TileJSON
   * renders a blank map, and every surface called it "done".
   *
   * A SHARED BASE LAYER is the exception: it is tiles and nothing else,
   * because the area downloads that read it carry the style, TileJSON and
   * sprite between them (SNOW-856). Its empty dependency list is not
   * "nothing was recorded" but "there is nothing to record".
   *
   * @param {AreaReading} area
   * @returns {{status: 'ready'|'incomplete'|'missing'|'unverifiable'|'unreadable',
   *   missingDeps: string[], tiles: number}}
   */
  function areaState(area) {
    var entries = Array.isArray(area.entries) ? area.entries : [];
    var deps = Array.isArray(area.deps) ? area.deps : [];
    var missingDeps = missingFrom(deps, entries);
    var declared = new Set(deps);
    var supporting = 0;
    for (var i = 0; i < entries.length; i += 1) {
      if (declared.has(entries[i])) supporting += 1;
    }
    var status = 'ready';
    if (area.bucketReadable === false) {
      // The bucket read did not come back. "Nothing is stored, download it
      // again" is a serious thing to say about a 200 MB area the user
      // chose on purpose, and a read that overran its budget is not
      // evidence for it — the tiles may be sitting there untouched.
      status = 'unreadable';
    } else if (area.bucketPresent === false || entries.length === 0) {
      status = 'missing';
    } else if (missingDeps.length > 0) {
      status = 'incomplete';
    } else if (deps.length === 0 && area.kind !== 'base') {
      // Downloaded before SNOW-844, so nothing on the record says what
      // that run fetched. The tiles are demonstrably there; whether the
      // style, TileJSON and sprite are cannot be answered from here.
      status = 'unverifiable';
    }
    return {
      status:
        /** @type {'ready'|'incomplete'|'missing'|'unverifiable'|'unreadable'} */ (
          status
        ),
      missingDeps: missingDeps,
      tiles: entries.length - supporting,
    };
  }

  /**
   * The areas the user chose, excluding the shared base layer.
   *
   * The base layer is stored, takes space and is real, but it is not a
   * place: it is the zoomed-out tiles every download reads, fetched by the
   * app for itself. "The map draws" is a question about the ground the
   * user asked for.
   *
   * @param {AuditReadings} r
   * @returns {AreaReading[]}
   */
  function chosenAreas(r) {
    return (Array.isArray(r.areas) ? r.areas : []).filter(function (area) {
      return area && area.kind !== 'base';
    });
  }

  /**
   * Answer one row.
   *
   * Every branch returns the same small shape: a status, and whatever the
   * summary needs in order to say something about a No. Nothing here is a
   * sentence — sentences are composed once, in ``composeSummary``.
   *
   * @param {string} id
   * @param {AuditReadings} r
   * @param {Record<string, string>} t
   * @returns {{status: AuditStatus, reason?: string, group?: string,
   *   effect?: string, note?: string}}
   */
  function answer(id, r, t) {
    var sw = r.serviceWorker || {};

    // A run that could not be taken answers nothing. Every row here reads
    // a field off `r`, and an absent field is indistinguishable from a
    // field read as empty — so a crashed collection would otherwise have
    // produced a confident "offline mode has not been set up on this
    // device" about a device whose worker is running perfectly. The panel
    // may say it does not know; it may not make something up.
    if (r.failure) return { status: 'unknown' };

    // The same rule one level down, for the four rows read out of Cache
    // Storage. `shellEntries` comes back `[]` both for a device holding
    // nothing and for one whose caches did not answer, and telling
    // somebody "The app will not open" over an app that opens is the one
    // failure this panel cannot survive.
    var CACHE_ROWS = [
      'app-opens',
      'app-looks-right',
      'danger-ratings',
      'region-shapes',
      'bulletins',
    ];
    if (r.cachesReadable === false && CACHE_ROWS.indexOf(id) >= 0) {
      return { status: 'unknown' };
    }

    // The narrower case: the LISTING came back, but opening a page in it
    // did not. The rows answered from URLs alone (the boot feeds) are
    // still answerable; the ones needing a page's principal stamp or its
    // body are not. Kept apart from the flag above so one unread body
    // does not blank five rows.
    var PAGE_ROWS = ['app-opens', 'app-looks-right', 'bulletins'];
    if (r.shellPartial === true && PAGE_ROWS.indexOf(id) >= 0) {
      return { status: 'unknown' };
    }

    if (id === 'offline-mode') {
      if (!sw.supported) return { status: 'unknown', note: s(t, 'note-sw-unsupported') };
      if (sw.controlled) return { status: 'yes' };
      return {
        status: 'no',
        reason: sw.registered ? 'starting' : 'absent',
        note: s(t, sw.registered ? 'note-sw-starting' : 'note-sw-absent'),
      };
    }

    if (id === 'app-opens') {
      // The HTML and the scripts together, because separating them was a
      // row nobody could read: "the app is complete" meant "the shell's
      // JavaScript is cached", and a page whose HTML is saved and whose
      // scripts are not does not open — it paints a blank frame, which to
      // the person holding the phone is indistinguishable from the page
      // never having been saved.
      var page = pages(r);
      if (!page.mapEntry) return { status: 'no', reason: 'absent' };
      if (!principalMatches(page.mapEntry.principal, r.currentPrincipal)) {
        return {
          status: 'no',
          reason: 'principal',
          note: fill(s(t, 'note-other-account'), {
            stamped: describePrincipal(page.mapEntry.principal, t),
          }),
        };
      }
      // The page's OWN modules, not "some JavaScript is cached". See
      // pageDependencies. A null reading is an entry whose body could not
      // be read at all, which answers No rather than Yes: warming
      // overwrites it, so the remedy this report offers still applies.
      if (r.mapDependencies === null || r.mapDependencies === undefined) {
        return { status: 'no', reason: 'unreadable' };
      }
      // Scripts only. The stylesheets are the next row's question, and an
      // app that opens unstyled is ugly and usable where one that does not
      // open is neither — folding them together would block the verdict
      // over a missing stylesheet.
      var scripts = dependenciesOfKind(r.mapDependencies, 'js');
      if (missingFrom(scripts, urlsIn(r)).length > 0) {
        return { status: 'no', reason: 'scripts' };
      }
      if (scripts.length === 0) {
        // A page that names no JavaScript at all. Unknown, not Yes — the
        // same reading `missingFrom` gives an area that claimed no render
        // dependencies.
        return { status: 'unknown' };
      }
      return { status: 'yes' };
    }

    if (id === 'app-looks-right') {
      // Styling on its own, and NOT critical: an unstyled app is ugly and
      // usable, where an app that will not open is neither. This is the
      // row that tells someone why the thing they opened looks wrong.
      //
      // SNOW-914: the map page's OWN stylesheets, for the same reason the
      // row above asks for its own scripts. `fileCounts(r).style > 0` was
      // "is any CSS cached", which the settings page's own stylesheet makes
      // true on the very device reading this panel — so the row said the
      // app would look right while the map's stylesheet was absent.
      if (r.mapDependencies === null || r.mapDependencies === undefined) {
        return { status: 'unknown' };
      }
      var styles = dependenciesOfKind(r.mapDependencies, 'css');
      if (styles.length === 0) return { status: 'unknown' };
      return missingFrom(styles, urlsIn(r)).length === 0
        ? { status: 'yes' }
        : { status: 'no', group: 'open-map', effect: s(t, 'effect-styles') };
    }

    if (id === 'no-downloads') {
      return { status: 'no', reason: 'none', note: s(t, 'note-no-areas') };
    }

    if (id === 'danger-ratings') {
      // The feed for the day the cached page will OPEN ON — its own
      // ``data-today``, which is the date its boot fetch will put in the
      // URL. Not this device's clock and not "any ratings at all": the row
      // prefix-matched ``/api/ratings/`` and said Yes for a feed from any
      // day, on a device whose map would open to a blank choropleth. Open
      // the app at home on Tuesday, open it on the mountain on Wednesday —
      // that is the journey this app is for, and the row was wrong in it.
      if (!r.mapDay) return { status: 'unknown' };
      return hasBootFeed(r, '/api/ratings/', { d: r.mapDay, country: BOOT_COUNTRY })
        ? { status: 'yes' }
        : {
            status: 'no',
            group: 'open-map',
            effect: s(t, 'effect-ratings'),
          };
    }

    if (id === 'region-shapes') {
      // The boot asks for one country's outlines. A device holding only
      // another country's would have read Yes and drawn nothing.
      return hasBootFeed(r, '/api/regions.geojson', { country: BOOT_COUNTRY })
        ? { status: 'yes' }
        : {
            status: 'no',
            group: 'open-map',
            effect: s(t, 'effect-shapes'),
          };
    }

    if (id === 'bulletins') {
      // SNOW-915: a bulletin URL, not "a cached page that is not the map".
      // The old test counted every other public page — /help/, /privacy/,
      // /colophon/, /compare/, /trips/, a shared trip — so reading Help
      // once told the user their bulletins were saved. The row says
      // "Bulletins you have opened", and a reader takes it at its word.
      var readable = pages(r).usable.filter(function (entry) {
        return isBulletinPath(pathOf(entry.url));
      });
      return readable.length > 0
        ? { status: 'yes' }
        : { status: 'no', note: s(t, 'note-no-bulletins') };
    }

    if (id === 'saved-places') {
      if (!r.dbAvailable) return { status: 'unknown' };
      var stored = Number((r.stores || {})['data:favourites']);
      var favourites = overlayState(r, 'favourites');
      if (favourites.status === 'yes') return { status: 'yes' };
      // The dedicated favourites store is a second, independent copy: the
      // pins sheet reads it whether or not the map overlay was ever
      // fetched, so rows there are shown even when the overlay is absent.
      if (Number.isFinite(stored) && stored > 0) return { status: 'yes' };
      return overlayAnswer(favourites, t, 'favourites');
    }

    if (id === 'routes') {
      if (!r.dbAvailable) return { status: 'unknown' };
      return overlayAnswer(overlayState(r, 'routes'), t, 'routes');
    }

    if (id === 'reports') {
      if (!r.dbAvailable) return { status: 'unknown' };
      var reports = overlayState(r, 'community_reports');
      if (reports.status === 'yes') return { status: 'yes' };
      // The observations panel is its own cached surface, and having read
      // one is having something to read offline whatever the map overlay
      // holds.
      if ((r.panelKeys || []).indexOf('observations') >= 0) return { status: 'yes' };
      return overlayAnswer(reports, t, 'reports');
    }

    if (id === 'weather') {
      if (!r.dbAvailable) return { status: 'unknown' };
      return overlayAnswer(overlayState(r, 'weather'), t, 'weather');
    }

    return { status: 'unknown' };
  }

  /**
   * Answer one downloaded area — the row the whole feature exists for.
   *
   * Four outcomes, and the distinction between them is the point: an area
   * whose tiles are all present and whose TileJSON is missing draws
   * nothing, and every surface before this one called it downloaded
   * (SNOW-843).
   *
   * @param {AreaReading} area
   * @param {Record<string, string>} t
   * @returns {{status: AuditStatus, reason?: string, note?: string}}
   */
  function answerArea(area, t) {
    var state = areaState(area);
    var name = area.name || area.id;
    if (state.status === 'ready') return { status: 'yes' };
    if (state.status === 'incomplete') {
      return {
        status: 'no',
        reason: 'incomplete',
        note: fill(s(t, 'note-area-incomplete'), { name: name }),
      };
    }
    if (state.status === 'missing') {
      return {
        status: 'no',
        reason: 'missing',
        note: fill(s(t, 'note-area-missing'), { name: name }),
      };
    }
    if (state.status === 'unreadable') {
      return {
        status: 'unknown',
        reason: 'unreadable',
        note: fill(s(t, 'note-area-unreadable'), { name: name }),
      };
    }
    return {
      status: 'unknown',
      reason: 'unverifiable',
      note: fill(s(t, 'note-area-unverifiable'), { name: name }),
    };
  }

  /**
   * Which download kind an area record belongs to.
   *
   * Mirrors ``basemap_manage_core.js``'s ``manageRows``: a drop zone is
   * its own kind rather than a custom area with a particular name, and an
   * older record that predates the ``type`` field falls back to the shape
   * it was.
   *
   * @param {AreaReading & {type?: string}} area
   * @returns {'region'|'dropzone'|'custom'|'base'}
   */
  function areaKind(area) {
    if (area.kind === 'base') return 'base';
    if (area.kind === 'region') return 'region';
    return area.type === 'dropzone' ? 'dropzone' : 'custom';
  }

  /**
   * The basemaps this device holds anything for, in a stable order.
   *
   * Every download is made UNDER a basemap and records which one, and the
   * shared low-zoom layer is stored per basemap too (SNOW-856). Rolling
   * them up is the answer to the question the per-area rows cannot
   * reach — *which map style will I actually see* — and that question had
   * no surface at all before this: a device could hold a complete
   * Swisstopo download and be sitting on OpenFreeMap, and nothing said so.
   *
   * @param {AuditReadings} r
   * @returns {string[]} Basemap keys.
   */
  function basemapsFor(r) {
    var keys = /** @type {string[]} */ ([]);
    (Array.isArray(r.areas) ? r.areas : []).forEach(function (area) {
      var key = area.basemapKey;
      if (!key || keys.indexOf(key) >= 0) return;
      keys.push(key);
    });
    keys.sort();
    // SNOW-913: the style ON SCREEN comes first, and is present whether or
    // not this device has stored a byte for it. Rolling up only what is
    // stored answered a question nobody asked: a reader who has switched to
    // Swisstopo was told about OpenFreeMap — a row naming a basemap they are
    // not looking at, and no row at all for the one they are. A report that
    // disagrees with what the reader can see is worth less than no report.
    var current = r.selectedBasemap;
    if (!current) return keys;
    return [current].concat(
      keys.filter(function (key) {
        return key !== current;
      }),
    );
  }

  /**
   * Answer one basemap: will this style render offline?
   *
   * Two halves, and both have to be there. The STYLE half is its render
   * dependencies — the style document, each source's TileJSON, the
   * sprite — without which MapLibre cannot learn a single tile URL and
   * the map is blank however many tiles are pinned (SNOW-843). The REACH
   * half is the shared z0–7 base layer: without it the map falls off the
   * edge of every downloaded area the moment the camera pulls out past
   * z10, which is SNOW-856's whole bug and is why "zoomed-out overview"
   * is a real question rather than an implementation detail. It is
   * answered here, beside the style it belongs to, rather than as a row
   * of its own among the downloads — it is not a place anyone chose.
   *
   * @param {string} key
   * @param {AuditReadings} r
   * @param {Record<string, string>} t
   * @returns {{status: AuditStatus, reason?: string, note?: string}}
   */
  function answerBasemap(key, r, t) {
    var areas = (Array.isArray(r.areas) ? r.areas : []).filter(function (area) {
      return area.basemapKey === key;
    });
    var styled = areas.some(function (area) {
      return area.kind !== 'base' && areaState(area).status === 'ready';
    });
    var reaches = areas.some(function (area) {
      return area.kind === 'base' && areaState(area).status === 'ready';
    });
    var name = basemapName(key, t);
    if (!styled) {
      return {
        status: 'no',
        reason: 'style',
        note: fill(s(t, 'note-basemap-unstyled'), { name: name }),
      };
    }
    if (!reaches) {
      return {
        status: 'no',
        reason: 'reach',
        note: fill(s(t, 'note-basemap-no-overview'), { name: name }),
      };
    }
    return { status: 'yes' };
  }

  /**
   * A basemap's name, as the picker says it.
   *
   * The strings table carries one ``basemap-<key>`` entry per style, so
   * the report and the picker cannot drift; an unknown key falls back to
   * itself rather than being dropped, because a style this device has
   * tiles for is worth a row whatever it is called.
   *
   * @param {string} key
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function basemapName(key, t) {
    var label = t && t['basemap-' + key];
    return typeof label === 'string' && label ? label : key;
  }

  /**
   * A download's row label — its name, and what kind of thing it is where
   * that is not obvious.
   *
   * A region is labelled by name alone: "Martigny — Verbier" is a place
   * and reads as one. The two kinds the user made themselves are not, so
   * they say which they are — the alternative was three section headings
   * for three kinds, which on a device with one of each put an
   * empty-looking heading between every pair of rows.
   *
   * @param {AreaReading & {type?: string}} area
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function areaLabel(area, t) {
    var name = area.name || area.id;
    var kind = areaKind(area);
    if (kind === 'dropzone') return fill(s(t, 'label-dropzone'), { name: name });
    if (kind === 'custom') return fill(s(t, 'label-custom'), { name: name });
    return name;
  }

  /**
   * Every row of the report, in paint order — the fixed capabilities plus
   * one per downloaded area.
   *
   * @param {AuditReadings|null} readings Null for the waiting skeleton,
   *   which cannot know the downloads yet.
   * @param {Record<string, string>} t
   * @returns {Array<{id: string, section: string, critical: boolean,
   *   label: string, area?: AreaReading, basemap?: string}>}
   */
  function rowsFor(readings, t) {
    var rows = /** @type {Array<{id: string, section: string,
      critical: boolean, label: string, area?: AreaReading,
      basemap?: string}>} */ (
      ROWS.map(function (row) {
        return {
          id: row.id,
          section: row.section,
          critical: row.critical,
          label: s(t, 'row-' + row.id),
        };
      })
    );
    if (readings) {
      // In THE MAP, after ratings and outlines: a basemap is what the map
      // is drawn ON, so it belongs with the other things the map needs
      // rather than in the list of places the user downloaded.
      basemapsFor(readings).forEach(function (key) {
        // SNOW-913: the row for the style on screen says so. Two rows
        // reading "X basemap" and "Y basemap" give a reader no way to tell
        // which one is theirs, and the whole point of naming the current
        // one is that it cannot be mistaken for the other.
        var current = key === readings.selectedBasemap;
        rows.push({
          id: 'basemap:' + key,
          section: 'map',
          critical: false,
          label: fill(s(t, current ? 'row-basemap-current' : 'row-basemap'), {
            name: basemapName(key, t),
          }),
          basemap: key,
        });
      });
    }
    var areas = readings ? chosenAreas(readings) : [];
    if (readings && areas.length === 0) {
      // The one row that exists only when there is nothing to list, so a
      // device with no downloads still gets a line saying so rather than
      // a heading over a gap.
      rows.push({
        id: 'no-downloads',
        section: 'downloads',
        critical: false,
        label: s(t, 'row-no-downloads'),
      });
      return rows;
    }
    DOWNLOAD_KIND_ORDER.forEach(function (kind) {
      areas
        .filter(function (area) {
          return areaKind(area) === kind;
        })
        .forEach(function (area) {
          rows.push({
            id: 'area:' + area.id,
            section: 'downloads',
            critical: false,
            label: areaLabel(area, t),
            area: area,
          });
        });
    });
    return rows;
  }

  /**
   * Build the sections and their rows, either answered or waiting.
   *
   * One function for both, because they must produce the same list in the
   * same order: the panel paints the waiting version first and fills each
   * answer into the row already on screen.
   *
   * @param {AuditReadings|null} readings Null for the waiting skeleton.
   * @param {Record<string, string>} t
   * @returns {AuditSection[]}
   */
  function buildSections(readings, t) {
    var rows = rowsFor(readings, t);
    return SECTIONS.map(function (section) {
      return {
        id: section.id,
        title: s(t, section.key),
        checks: rows
          .filter(function (row) {
            return row.section === section.id;
          })
          .map(function (row) {
            if (!readings) {
              return /** @type {AuditCheck} */ ({
                id: row.id,
                label: row.label,
                value: s(t, 'answer-pending'),
                status: /** @type {AuditStatus} */ ('pending'),
              });
            }
            var resolved = row.area
              ? answerArea(row.area, t)
              : row.basemap
                ? answerBasemap(row.basemap, readings, t)
                : answer(row.id, readings, t);
            var status = resolved.status;
            // A No on a critical row is the reason the whole thing fails,
            // not one capability being unavailable — the log says so with
            // a third colour rather than a fourth word.
            if (status === 'no' && row.critical) status = 'blocked';
            return /** @type {AuditCheck} */ (
              Object.assign({}, resolved, {
                id: row.id,
                label: row.label,
                value: s(t, 'answer-' + (status === 'blocked' ? 'no' : status)),
                status: status,
              })
            );
          }),
      };
    }).filter(function (section) {
      // A kind with no downloads renders nothing at all, rather than an
      // empty heading over a gap.
      return section.checks.length > 0;
    });
  }

  /**
   * The one-line answer, which is the worst true thing in the report.
   *
   * Ordered by what the user would do about it rather than by severity:
   * no worker at all comes first because nothing below it can be true,
   * and a missing map page comes before a missing download because the
   * download cannot be reached without the page.
   *
   * @param {AuditSection[]} sections
   * @param {Record<string, string>} t
   * @param {AuditReadings} [readings] For the one verdict that is not read
   *   off the rows: a collection that threw has no rows worth reading.
   * @returns {{status: 'ok'|'warn'|'fail', text: string, covers: string[]}}
   *   ``covers`` names the rows this sentence has accounted for, so
   *   ``composeSummary`` does not say them a second time.
   */
  function verdictFor(sections, t, readings) {
    var r = readings || {};
    // A collection that threw has no findings, so it gets no diagnosis.
    // This is `fail` rather than `warn` for the reason `verdict-no-worker`
    // is: the thing the reader came here to learn is not knowable, and a
    // warn-tinted verdict over sixteen dashes reads as "mostly fine".
    if (r.failure) {
      return { status: 'fail', text: s(t, 'verdict-failed'), covers: [] };
    }
    var byId = /** @type {Record<string, AuditCheck>} */ ({});
    sections.forEach(function (section) {
      section.checks.forEach(function (check) {
        byId[check.id] = check;
      });
    });
    var blocked = /** @param {string} id */ function (id) {
      return byId[id] && byId[id].status === 'blocked';
    };

    if (blocked('offline-mode')) {
      return {
        status: 'fail',
        text: s(t, 'verdict-no-worker'),
        covers: ['offline-mode'],
      };
    }
    if (blocked('app-opens')) {
      return {
        status: 'fail',
        text: s(
          t,
          byId['app-opens'].reason === 'principal'
            ? 'verdict-other-account'
            : 'verdict-no-page',
        ),
        covers: ['app-opens'],
      };
    }
    if (byId['no-downloads']) {
      return { status: 'warn', text: s(t, 'verdict-no-map'), covers: ['no-downloads'] };
    }
    // Every download failing is a different answer from some of them
    // failing: the first means there is nothing to look at, the second
    // means the user has to know WHICH, and only the summary has room.
    var areaRows = /** @type {AuditCheck[]} */ ([]);
    sections.forEach(function (section) {
      section.checks.forEach(function (check) {
        if (check.id.indexOf('area:') === 0) areaRows.push(check);
      });
    });
    var drawable = areaRows.filter(function (check) {
      return check.status === 'yes' || check.status === 'unknown';
    });
    if (areaRows.length > 0 && drawable.length === 0) {
      return { status: 'warn', text: s(t, 'verdict-downloads-broken'), covers: [] };
    }
    // Nothing critical is broken, but "everything you need is here" over
    // a table with six Nos in it is the exact species of reassurance this
    // whole feature exists to stop being given. The all-clear is the
    // all-clear, and anything less says so — including a row nothing
    // could read. An unknown is not a Yes, and a device whose IndexedDB
    // would not open has not been checked, whatever the rest of the table
    // says.
    var anyNo = false;
    var anyUnknown = false;
    sections.forEach(function (section) {
      section.checks.forEach(function (check) {
        if (check.status === 'no' || check.status === 'blocked') anyNo = true;
        if (check.status === 'unknown') anyUnknown = true;
      });
    });
    if (anyNo) return { status: 'warn', text: s(t, 'verdict-partial'), covers: [] };
    if (anyUnknown) {
      return { status: 'warn', text: s(t, 'verdict-unchecked'), covers: [] };
    }
    return { status: 'ok', text: s(t, 'verdict-ok'), covers: [] };
  }

  /**
   * The paragraph under the verdict — what to expect, and what fixes it.
   *
   * This is the half a non-technical reader actually reads, and the reason
   * no row carries an explanation of its own. It composes rather than
   * concatenates, in two tiers:
   *
   * **The grouped sentence.** Capabilities missing for one shared reason
   * are said once: their effects run together after a shared lead, and
   * one remedy sentence closes it. Three rows answering No for the same
   * reason produce one sentence naming one action, rather than three
   * sentences naming the same one.
   *
   * **The notes.** Everything else contributes one clause, joined into a
   * closing sentence — the Nos with nothing in common.
   *
   * Whatever the verdict already said is left out entirely (``covers``).
   *
   * @param {AuditSection[]} sections
   * @param {{status?: string, covers: string[]}} verdict
   * @param {Record<string, string>} t
   * @param {AuditReadings} [readings] For the leading clause naming why
   *   some rows are dashes.
   * @returns {string} ``''`` when nothing is left to say, and always for
   *   a ``fail`` verdict — see the first branch.
   */
  function composeSummary(sections, verdict, t, readings) {
    var r = readings || {};
    // Nothing below a blocked critical row is reachable, so nothing below
    // it is worth advising on. A device with no service worker was being
    // told to open the map once while connected to fix its styling — true
    // in the abstract, useless in the specific, and three sentences of it
    // under a verdict that had already named the one thing to do. The log
    // still shows every row; the paragraph stops pretending the rest is
    // actionable.
    if (verdict.status === 'fail') return '';
    var covered = new Set(verdict.covers || []);
    // Why some rows are dashes. It leads rather than joining the notes
    // sentence because it is the reason the rest of the paragraph is
    // shorter than it should be, and a reader who does not know that
    // reads a partial report as a complete one.
    var lead = /** @type {string[]} */ ([]);
    if (r.degraded) lead.push(s(t, 'note-storage-slow'));
    var groups = /** @type {Record<string, AuditCheck[]>} */ ({});
    var notes = /** @type {string[]} */ ([]);

    sections.forEach(function (section) {
      section.checks.forEach(function (check) {
        if (covered.has(check.id)) return;
        if (check.group) {
          if (!groups[check.group]) groups[check.group] = [];
          groups[check.group].push(check);
        } else if (check.note) {
          notes.push(check.note);
        }
      });
    });

    var sentences = /** @type {string[]} */ (lead.slice());
    Object.keys(groups).forEach(function (key) {
      var members = groups[key];
      var effects = /** @type {string[]} */ (
        members
          .map(function (check) {
            return check.effect;
          })
          .filter(Boolean)
      );
      sentences.push(
        fill(s(t, 'group-' + key + '-lead'), { effects: joinList(effects, t) }),
      );
      sentences.push(
        fill(s(t, 'group-' + key + '-remedy'), { count: quantify(members.length, t) }),
      );
    });

    if (notes.length > 0) {
      // Capped, and the cap is the point. Every No contributing a clause
      // produced a six-line run-on that read as an inventory — which is
      // what the table above already is, and what this paragraph exists
      // not to be. Notes arrive in row order, which is importance order,
      // so the three that survive are the three worth a sentence; the
      // rest are visible one line up, spelled out, in the log.
      sentences.push(
        fill(s(t, 'notes-sentence'), { notes: joinList(notes.slice(0, 3), t) }),
      );
    }

    return sentences.join(' ');
  }

  /**
   * How many capabilities were asked about, and how many answered Yes.
   *
   * ``unknown`` is in neither figure: a reading that could not be taken is
   * not a capability this device has or lacks, and counting it either way
   * would be a claim the report cannot support.
   *
   * @param {AuditSection[]} sections
   * @returns {{total: number, yes: number, no: number}}
   */
  function countChecks(sections) {
    var total = 0;
    var yes = 0;
    var no = 0;
    sections.forEach(function (section) {
      section.checks.forEach(function (check) {
        total += 1;
        if (check.status === 'yes') yes += 1;
        if (check.status === 'no' || check.status === 'blocked') no += 1;
      });
    });
    return { total: total, yes: yes, no: no };
  }

  /**
   * The waiting skeleton — every row, no answers.
   *
   * Painted before the first reading lands, so the panel shows the whole
   * list of questions from the moment it opens rather than growing out of
   * nothing one row at a time.
   *
   * @param {Record<string, string>} [strings]
   * @returns {AuditReport}
   */
  function pendingReport(strings) {
    var t = strings || {};
    var sections = buildSections(null, t);
    return {
      verdict: { status: 'pending', text: '' },
      summary: '',
      sections: sections,
      counts: countChecks(sections),
      generatedAt: new Date().toISOString(),
      pending: true,
    };
  }

  /**
   * Build the answered report.
   *
   * Every reading is optional. A device broken enough to fail these reads
   * is exactly the device whose user is reading this, so a missing input
   * degrades to a stated ``unknown`` rather than a thrown error.
   *
   * @param {AuditReadings} [readings]
   * @param {Record<string, string>} [strings]
   * @returns {AuditReport}
   */
  function buildReport(readings, strings) {
    var r = readings || {};
    var t = strings || {};
    var sections = buildSections(r, t);
    var verdict = verdictFor(sections, t, r);
    return {
      verdict: { status: verdict.status, text: verdict.text },
      summary: composeSummary(sections, verdict, t, r),
      // What the run could not read, carried through to `reportText` —
      // the only route this has off a phone with no devtools.
      degraded: r.degraded || null,
      failure: r.failure || null,
      sections: sections,
      counts: countChecks(sections),
      generatedAt: typeof r.now === 'string' ? r.now : new Date().toISOString(),
      pending: false,
    };
  }

  /**
   * The report as plain text, for the Copy control.
   *
   * A phone with no devtools is the only place this data exists, so
   * getting it off the device is not a convenience. The summary comes
   * first, because whoever is pasted this reads it the same way round the
   * panel is read.
   *
   * @param {AuditReport} report
   * @param {{userAgent?: string, appVersion?: string, url?: string}} [context]
   * @returns {string}
   */
  function reportText(report, context) {
    var ctx = context || {};
    var lines = ['Snowdesk — what works offline', report.generatedAt];
    if (ctx.url) lines.push(ctx.url);
    if (ctx.appVersion) lines.push('app version: ' + ctx.appVersion);
    if (ctx.userAgent) lines.push(ctx.userAgent);
    lines.push('');
    lines.push(report.verdict.text);
    if (report.summary) lines.push(report.summary);
    lines.push('');
    lines.push(report.counts.yes + ' of ' + report.counts.total + ' available offline');
    // The diagnostic half, and the reason Copy is offered on a failed run
    // at all. Whoever is sent this needs to know that the report is
    // partial and which reads are the ones that did not come back —
    // "Checking… for ever" was a bug with no evidence anywhere on the
    // device, and this is the evidence.
    if (report.failure) {
      lines.push('');
      lines.push('the check did not run: ' + report.failure);
    }
    if (report.degraded) {
      lines.push('');
      lines.push(
        'readings that did not answer: ' + (report.degraded.timedOut || []).join(', '),
      );
      if (report.degraded.latched) {
        lines.push('the run stopped early: this device’s storage stopped answering');
      }
    }
    report.sections.forEach(function (section) {
      lines.push('');
      lines.push('## ' + section.title);
      section.checks.forEach(function (check) {
        lines.push('  [' + check.status + '] ' + check.label + ': ' + check.value);
      });
    });
    return lines.join('\n');
  }

  self.pwaOfflineAuditCore = Object.freeze({
    ROW_IDS: Object.freeze(
      ROWS.map(function (row) {
        return row.id;
      }),
    ),
    buildReport: buildReport,
    pendingReport: pendingReport,
    reportText: reportText,
    composeSummary: composeSummary,
    countChecks: countChecks,
    missingFrom: missingFrom,
    classifyEntry: classifyEntry,
    pageDependencies: pageDependencies,
    pageDay: pageDay,
    pageBasemaps: pageBasemaps,
    resolveBasemap: resolveBasemap,
    formatBytes: formatBytes,
    principalMatches: principalMatches,
    areaState: areaState,
    joinList: joinList,
    quantify: quantify,
  });
})();
