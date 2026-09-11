---
name: offline-audit
description: Offline-content report — offline_audit.js, offline_audit_core.js, X-SW-Principal page check, AUDIT_SCRIPTS precache, offline.html panel
status: current
last-reviewed: 2026-09-11
---

# The offline-content report (SNOW-907)

An on-device audit of what this browser can actually show without a
signal. One verdict, then the evidence behind it.

It exists because of a journey that went wrong in a way nothing on the
device could explain: a region deliberately downloaded while connected,
and then, underground, `static/offline.html` — "this page isn't available
offline" — with no map and no content.

## Why nothing could answer that

What makes Snowdesk work offline lives in four stores that know nothing
about each other, and each surface reporting on one of them reports only
its own half:

| What must be true | Where it lives | Who reported it before |
|---|---|---|
| The page HTML is in the shell cache **and** its `X-SW-Principal` stamp equals the principal signed in now | `snowdesk-shell-<hash>` | **nobody** |
| The shell's JS and CSS are in that same cache | `snowdesk-shell-<hash>` | nobody |
| An area's tiles are in its pinned bucket | `snowdesk-basemap-pinned-<id>` | Manage downloads sheet |
| Its style, TileJSON and sprite are too | same bucket | Manage downloads sheet |
| The map's data feeds are cached | shell cache + `data:*` stores | the layers menu's sync dots |
| Nothing is stuck unsent | `queue:mutations` | the reset panel's count |

The first two rows are the ones that produce that exact symptom, and they
were the two nothing measured. A downloaded region is irrelevant if the
map page's own HTML was never cached — or was cached under a different
account, which `sw.js`'s `_networkFirstFallback` refuses to serve and
then falls through to `offline.html` without saying so anywhere.

## Where it is

**Two hosts, one module.** `static/js/offline_audit.js` paints the same
report into both:

- **`/account/settings/` → This device → Offline content.** The
  proactive one: the surface to use *before* a journey. Sits above Reset
  local data on purpose — the two answer the same question, and until
  this existed, wiping every download was the only answer on the page.
- **`static/offline.html`.** The reactive one, and the reason the module
  is precached at all: settings is a Django view, so it loads only if it
  happens to be in the shell cache for this account. The page a stuck
  user *can* open is the one that has to carry this.

The app-side markup and its `{% trans %}` strings are
`templates/includes/_offline_audit_panel.html`; the offline page carries
the same markup contract inline, and English copy comes from the
module's own `FALLBACKS`.

## The five sections

Each check resolves to **ok / warn / fail / unknown**, and the verdict is
the worst thing that is true, said in one line.

1. **This device** — is a worker registered and in control; is an update
   waiting; `storage.estimate()` and `persisted()`; whether the
   connection is forced off (the user's choice) or latched off (the
   worker gave up after three timeouts).
2. **Pages saved for offline** — every `text/html` entry in the shell
   cache, each with its principal stamp and whether that matches
   `meta:app`'s `mutations.principal`. `/` is checked by name and drives
   the verdict. **This is the check that explains the tube.**
3. **App files** — the shell-cache inventory by kind. A page whose HTML
   is cached and whose scripts are not opens blank, which to a user is
   indistinguishable from not being saved at all.
4. **Downloaded maps** — one row per record in `basemap.regions`,
   `basemap.customAreas` and `basemap.baseLayers`: tiles present in the
   bucket, missing render dependencies from the record's own `deps`,
   size, and the two ways a device and its own records disagree (a
   record with no bucket, a bucket with no record).
5. **Saved data** — row counts for `data:favourites`,
   `data:map_overlays`, `data:panel_rows`, and the depth of
   `queue:mutations`.

## Three rules worth keeping

**A reading that could not be taken is `unknown`, never `ok`.** The
report is read by someone already let down once by a surface that said
everything was fine. `unknown` never counts towards the verdict in
either direction — a browser with no `storage.estimate()`, an
unreadable IndexedDB, an area whose record names no dependencies (one
downloaded before SNOW-844) each say so on their own row.

**It reads storage directly; it never asks another module.**
`pwaBasemapAreas`, `pwaBasemapDownloadCore` and `pwaDb` all expose
readers for most of this, and none is used. A report whose job is to
catch storage and the app disagreeing must not take the app's word for
what storage holds — SNOW-843 was three surfaces agreeing an area was
downloaded while the map drew nothing. It also has to run on
`offline.html`, where none of those modules exists.

The one consequence is a deliberate twelve-line restatement of
`missingRenderDependencies` inside `offline_audit_core.js`, against the
same contract. Importing it would mean precaching 116 KB of tile
arithmetic for a recovery page.

**It never writes.** Every read is read-only, `caches.has` gates every
bucket read (`caches.open` *creates* on miss, which would turn "this
area is gone" into "this area is empty" for every run after the first),
and IndexedDB is opened with no version so it cannot trigger an upgrade.
The one action goes through the worker.

## The one action

**Save the map page for offline** — `window.pwaWarmCache(['/'])`. Shown
only with a controlling worker, a connection, and a map page that is
actually missing for this account. It goes through the worker's own
`warm-cache` message rather than a `cache.put` from the page because
SNOW-624 made `_warmCache` stamp a same-origin HTML response with the
principal its body declares, and an unstamped entry is one the worker
refuses for ever.

**Copy report** puts the whole thing on the clipboard as text, for the
same reason `debug_log_panel.js` has a Copy: a phone with no devtools is
the only place this data exists.

## Precaching

`sw.js` warms both modules onto the device on install, by their unhashed
`/static/` paths — the ones `offline.html` requests, since production
serves hashed static URLs and ordinary browsing never puts these in the
shell cache.

They are in `AUDIT_SCRIPTS` and **not** in `PRECACHE_URLS`, which goes
through one atomic `cache.addAll` where a single failed entry fails
`install` and leaves the device with no worker at all. The audit is not
worth that risk: warmed individually through `Promise.allSettled`, a
missing file costs the panel and nothing else. Both hosts self-guard on
`window.pwaOfflineAudit` being present, the same way the reset panel
guards on `window.pwaResetLocalData`.

## Tests

| What | Where |
|---|---|
| The report model — verdicts, statuses, every degraded reading | `tests/js/test_offline_audit_core.js` |
| The collector and the rendered DOM | `tests/js/test_offline_audit.js` |
| Both hosts' markup, the strings-template drift check, the precache | `tests/accounts/test_settings_offline_audit.py` |

No Playwright test. Everything here is either arithmetic or a DOM
assertion jsdom can make, and `tests/e2e/` is a dozen smoke tests
([client-side-tests.md](client-side-tests.md)).
