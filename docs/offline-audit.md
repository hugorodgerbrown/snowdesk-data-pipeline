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

## What it asks

A **fixed** list of capabilities, each answered Yes or No. Not an
inventory — how many program files are cached, how many reports are
stored, how many saved places there are is not a question anyone has.
"Will the bulletins I have opened still open?" is.

| Section | Rows |
|---|---|
| Getting in | Offline mode is on · The app opens · The app is complete |
| The map | Danger ratings · Region outlines · Zoomed-out overview |
| Regions you downloaded | one row per region, by name |
| Drop zones you downloaded | one row per drop zone, by name |
| Areas you drew | one row per custom area, by name |
| Your content | Bulletins you have opened · Your saved places · Community reports · Weather |
| Keeping it | Safe from browser cleanup · Room for more · Changes you make are kept |

`ROWS` in `offline_audit_core.js` is a constant, declared before
anything is read, so a device with nothing stored produces the same rows
as a device with everything, all reading No. That is what makes the
table scannable, comparable between runs, and paintable before the first
reading lands.

**The downloads are the exception, deliberately.** One row saying the map
draws is no use to someone whose Verbier download is the broken one, so
every download gets a named row grouped by kind — which regions, which
drop zones, which areas they drew. A kind with no downloads renders no
section; a device with none of any kind gets a single "Map areas
downloaded — No".

Several rows are answered from more than one reading. "The app opens" is
the map page's HTML being in the shell cache **and** its
`X-SW-Principal` stamp matching the account signed in now — the two
halves of the failure that produced the ticket. The user does not care
which half; the summary says, the row does not.

## Two halves, doing two jobs

**The log is evidence.** One line per capability, and strictly one: label
and answer. Its job is to show that thirteen-odd separate things were
looked at, which is what makes the conclusion believable — nobody trusts
a single green tick from the app that just failed them.

**The summary is the answer.** A verdict sentence plus one paragraph of
ordinary prose saying what to expect and what fixes it, on a ground
tinted by the verdict. It is where detail a row cannot hold goes: which
area is incomplete, which account the saved page belongs to. Under both
sits the tally — "7 of 14 available offline".

The rule that keeps them apart: **a row never carries its own
explanation.** Per-row helper text made the log three times taller,
turned scanning into reading, and printed one shared remedy once per
row. `composeSummary` folds Nos with one shared remedy into a single
sentence — "Without a signal the app will show no danger ratings and
will draw no region outlines. Opening the map once while connected fixes
both." — and caps the loose clauses after it at three, because the table
above is already the inventory.

Whatever the verdict said is excluded from the paragraph: saying it twice
in three lines is how a summary starts reading like an error log.

### The build is an animation, and carries no timings

Every reading is taken before a single answer is painted. The rows then
fill in on a fixed `ROW_INTERVAL_MS` cadence. An earlier cut printed each
row's real elapsed time; on any ordinary device that was fifteen rows of
`0.00s`, which looked like precision and conveyed nothing.

What the build is for: the list of questions is on screen in full,
unanswered, from the first frame, and the reader watches each one get
settled. `prefers-reduced-motion` skips the cadence and paints the
finished report in one go — nothing is lost, because the report is
complete before the reveal starts.

## A No is not always a fault

Four rows are `critical`: offline mode, the app opening, the app being
complete, and having anything downloaded. A No on one of those blocks the
headline verdict and paints red — there is no point telling someone their
bulletins are saved if the app will not open. Every other No is amber: a
capability this device does not have offline, which is worth knowing and
is not an error.

## Three rules worth keeping

**A reading that could not be taken is `unknown`, never `yes`.** The
report is read by someone already let down once by a surface that said
everything was fine. An `unknown` row shows a dash and counts towards
neither half of the tally — a browser with no `storage.estimate()`, an
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
