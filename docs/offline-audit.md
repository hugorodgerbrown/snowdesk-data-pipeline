---
name: offline-audit
description: Offline-content report — offline_audit.js, offline_audit_core.js, bounded storage reads, X-SW-Principal check, AUDIT_SCRIPTS precache
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
| Getting in | Offline mode is on · The app opens · The app looks right |
| The map | Danger ratings · Region outlines · one row per basemap the device holds anything for |
| Map downloads | one row per download, by name |
| Your content | Bulletins you have opened · Your saved places · Your routes · Community reports · Weather |

`ROWS` in `offline_audit_core.js` is a constant, declared before
anything is read, so a device with nothing stored produces the same rows
as a device with everything, all reading No. That is what makes the
table scannable, comparable between runs, and paintable before the first
reading lands.

**The downloads are the exception, deliberately.** One row saying the map
draws is no use to someone whose Verbier download is the broken one, so
every download gets its own named row — under one heading, with the kind
in the label where it is not obvious ("La Chaux (drop zone)"). Three
headings for three kinds put an empty-looking section between every pair
of rows on a device with one of each. A device with nothing downloaded
gets a single "Anything downloaded at all — No".

**A basemap gets its own row, in The Map.** Every download is made under
a basemap and records which, and the shared low-zoom layer is stored per
basemap too — rolling them up answers the question the per-area rows
cannot reach: *which map style will I actually see*. A device could hold
a complete Swisstopo download and be sitting on OpenFreeMap, and nothing
said so.

**The style on screen always gets a row, and says so** (SNOW-913). The
roll-up above is over what the device has *stored*, and on its own that
answered a question nobody asked. A reader who had switched to Swisstopo
— whose wide-band warm had not completed, so no record for it existed —
was shown one row, "OpenFreeMap basemap: No", about a style they were not
looking at, and no row at all for the one they were. The report has to
agree with what the reader can see; a row naming a basemap they are not
using corrodes the panel faster than a missing row would.

So `selectedBasemap` is a reading like any other: `localStorage`'s
`snowdesk.map.basemap` (`BASEMAP_STORAGE_KEY`, written only when someone
opens the picker and chooses), falling back to the deployed default the
host page carries as `data-default-basemap-key` — `settings.BASEMAP`,
which only a server can say, and which is what an untouched device is
actually looking at.

**Resolved the way the map resolves it**, not just read. `map.js` checks a
stored key against the catalogue it renders and falls back to the default
when it no longer appears:

```js
const preferred = (stored && BASEMAP_OPTIONS[stored]) ? stored : DEFAULT_BASEMAP_KEY;
```

A style retired from the picker leaves its preference behind in
`localStorage`, so a report that read the key alone would label a basemap
"on screen" that the map will not show — the same defect this row exists
to fix, one level down. `pageBasemaps()` reads the catalogue and the
default out of the **cached** page's own markup (its `data-basemap-key`
buttons and `#map`'s `data-default-basemap-key`), because that page is the
one that will boot, and `resolveBasemap()` mirrors the line above against
it. With no cached page there is no catalogue to check against and the
stored key stands — the page being absent is already the blocking row. It is first in the list whether or not a byte is
stored for it, and its label is `row-basemap-current` ("… (on screen)")
rather than `row-basemap`, because two rows reading "X basemap" and "Y
basemap" leave the reader no way to tell which is theirs.

`static/offline.html` is a static file with no server to ask, so on a
device that has never opened the picker it names no current basemap at
all — an omission rather than a guess, which is the rule every other
reading here follows. The row is Yes only when both halves are there: the style
document, TileJSON and sprite (without which MapLibre cannot learn a
single tile URL — SNOW-843), and the z0–7 tiles (without which the map
falls off the edge of every downloaded area the moment the camera pulls
out past z10 — SNOW-856). That second half is why "zoomed-out" is a real
question; it is answered here rather than as a download row of its own,
because it is not a place anyone chose.

Two more rows are answered from more than one reading:

- **The app opens** is the map page's HTML being in the shell cache, its
  `X-SW-Principal` stamp matching the account signed in now, *and* every
  same-origin module that page's HTML boots from being cached too. The
  user does not care which of the three failed; the summary says, the row
  does not. A page whose HTML is saved and whose scripts are not paints a
  blank frame, which is indistinguishable from never having been saved —
  so it is one question, not two. (It was two, and the second was
  labelled "The app is complete", which meant nothing to anyone.)

  **The third clause is the page's own list, not a count** (SNOW-912).
  It read `fileCounts(r).script > 0` — *is any script cached* — which is
  true on every device that has a worker at all, because `AUDIT_SCRIPTS`
  precaches two modules on install. So a device holding the HTML and none
  of the map's JavaScript read Yes and opened to a blank frame. The
  collector now reads the cached page's body, `pageDependencies()` pulls
  the same-origin `.js`/`.css` out of it, and `missingFrom()` answers it
  the way a download row is answered — the rule in
  [`docs/decisions/a-downloaded-area-is-verified-by-what-it-renders.md`](decisions/a-downloaded-area-is-verified-by-what-it-renders.md),
  applied to the page. An unreadable body is No (`reason: 'unreadable'`),
  because warming overwrites the entry and the panel's own Save control
  is therefore still the remedy; a page that names nothing on a device
  holding nothing is `unknown`, because an empty claim is not a pass.

  `sw.js` has a second implementation of the extraction
  (`_shellSubresources`) — the worker is a classic script and would have
  to `importScripts` this whole module to share one. They are held to the
  same answers by a shared fixture table in `tests/js/test_sw.js`, the
  same shape that keeps sw.js's inline core fallbacks honest. A drift
  between them means the report verifies a page against a different list
  from the one the warm fetches, which is how a row goes green over a
  page that will not open.
- **The app looks right** is the styling on its own, and is *not*
  critical: an unstyled app is ugly and usable, where an app that will
  not open is neither. It asks the same list for its **stylesheets** that
  the row above asks for its scripts (SNOW-914) — `fileCounts(r).style > 0`
  was "is any CSS cached", which the settings page's own stylesheet makes
  true on the very device reading the panel.

## Every row answers about what the user will SEE

A row that says Yes to someone looking at a blank map costs more than the
row is worth, and a false green is the only failure this panel cannot
survive. Four rows were counting something adjacent to the question, and
each of them read Yes on a device that would have shown the user nothing
(SNOW-914/915):

| Row | Counted | Asks now |
|---|---|---|
| Danger ratings | any `/api/ratings/` entry | the feed for the day the cached page will open on |
| Region outlines | any `/api/regions.geojson` entry | the country the cold open asks for |
| Bulletins you have opened | any cached page that is not the map or an account page | a page whose path is a bulletin |
| Your saved places / routes / reports / weather | the overlay row existing | a row this account can read, holding something |

**The ratings row is the one that mattered most.** The map's cold open
fetches `RATINGS_URL + '?d=' + readDisplayDate() + '&country=ch'`, and
`readDisplayDate()` falls back to `#season-scrubber`'s `data-today` — the
day the **cached page** was rendered on, not the device's clock.
`_staleWhileRevalidate` matches exact URLs, so any other day's feed is a
miss and the choropleth paints nothing. The row prefix-matched the path
and said Yes for a feed from any day at all. Open the app at home on
Tuesday, open it on the mountain on Wednesday: blank map, green row —
which is the journey this app exists for. The collector now reads
`data-today` out of the cached HTML (`pageDay`) and the row asks for that
day's feed. `BOOT_COUNTRY` mirrors the country hard-coded in `map.js`; a
grep for it finds both sides.

**The bulletins row** counted every other public page, because every one
of them is cached by the visit that renders it — so reading `/help/` once
told the user their bulletins were saved. It now matches the region-id
shape Django routes bulletins on (`isBulletinPath`, restating
`RegionIdConverter.regex`, which is tight enough to reject `wp-login` and
so tight enough to reject `help`).

**The four content overlays** were answered by the presence of a row in
`data:map_overlays`. Presence is not readability: `getOverlay` returns
null for a row whose `principal` does not match the account signed in now
(favourites and routes are account-scoped, SNOW-493), so a row from
another session is on the device and invisible. Nor is presence content:
a row holding an empty FeatureCollection draws nothing. Both read Yes.
The collector now reads each row's feature count and stamp, and the three
states are told apart — Yes, No (absent, or another account's, with the
note saying which), and **unknown** for a row that is readable and empty,
because "you have no routes" is neither a capability nor a fault and
belongs on neither side of the tally.

## Two halves, doing two jobs

**The log is evidence.** One line per capability, and strictly one: label
and answer. Its job is to show that thirteen-odd separate things were
looked at, which is what makes the conclusion believable — nobody trusts
a single green tick from the app that just failed them.

**The summary is the answer.** A verdict sentence plus one paragraph of
ordinary prose saying what to expect and what fixes it, on a ground
tinted by the verdict. It is where detail a row cannot hold goes: which
area is incomplete, which account the saved page belongs to. Under both
sits the tally — "9 of 16 available offline".

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

Two rows are `critical`: offline mode being on, and the app opening. A No
on either blocks the headline verdict and paints red — there is no point
telling someone their bulletins are saved if the app will not open. Every
other No is amber: a capability this device does not have offline, which
is worth knowing and is not an error.

The all-clear is the all-clear, though. A single No anywhere downgrades
the verdict to "The app will open, but not everything will be there" —
"everything you need is on this device" printed over a table with six Nos
in it is the exact species of reassurance this feature exists to stop
being given.

## It always finishes

The report shipped able to hang. Every reading was an unbounded `await`
on Cache Storage or IndexedDB, `run()` was called unawaited from a click
handler with no `catch`, and the two together have exactly one visible
symptom: the skeleton rows on screen, unanswered, under a status line
reading **"Checking…"**, for ever.

Which is what happened, on an iPad, to the one reader this panel was
written for — someone whose app had frozen after an update insisting it
was offline, and who had already had to reset local data to recover.
Below it in the same screenshot, the reset panel read "Loading…". The two
have no code in common; what they share is a store that had stopped
answering and a `catch` written against a rejection it never sent.

Three rules now hold, and each one is a test:

**Every reading is time-bounded.** `bounded()` wraps every call into
Cache Storage, IndexedDB and `navigator.storage`: `READ_BUDGET_MS` (4s)
each, `COLLECT_BUDGET_MS` (20s) for the run, and a latch at
`READ_LATCH_THRESHOLD` (3) consecutive overruns so a device with a wedged
store and twelve downloads says so in about twelve seconds rather than
paying the budget once per read. Any reading that lands resets the count.
This is `sw.js`'s rule for the network, applied to storage — see
[`docs/decisions/bounded-offline-read-paths.md`](decisions/bounded-offline-read-paths.md),
whose "Extended to storage reads" section is the reasoning.

**A reading that overran is `unknown`, never an absence.** The rule three
sections down, which the bound had to be built around rather than
through. `cacheNames()` returns `null` rather than `[]` for a Cache
Storage it could not list, a bucket reading carries `readable`, and
`cachesReadable: false` sends the five cache-derived rows — `app-opens`,
`app-looks-right`, `danger-ratings`, `region-shapes`, `bulletins` — to
`unknown`. A bound that fell back to an absence would print **"The app
will not open without a signal"** over an app that opens perfectly, and
tell a user with 200 MB of downloaded maps that nothing was stored and
they should download them again. Those are worse than the hang.

**A run that throws is a report.** The collector's failure is caught,
every row reads `unknown`, the verdict is "This check could not run on
this device", and **Copy is offered** — carrying the thrown message and
the list of readings that did not answer. A phone with no devtools is the
only place that evidence will ever exist, which is what makes the copied
report the deliverable of a failed run rather than a consolation.

What the panel will not do is guess. A crashed run with no readings would
otherwise answer "offline mode has not been set up on this device" — a
confident, wrong diagnosis of a worker that is running fine, from a check
that never ran.

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
refuses for ever. SNOW-912 made that same path pull the page's
same-origin scripts and stylesheets too, so what the button saves is a
map page that opens rather than one that paints a blank frame.

The gate reads the `app-opens` check's `status` from
`offline_audit_core.js`, and offers the control for **every** failing
state. It once read a row called `map-page` and a status of `ok`, neither
of which the core has ever produced, so the lookup found nothing and the
button was hidden on every device — including the one whose verdict was
telling its owner, in red, to go and open the map. Any future gate here
names ids and statuses the core actually emits
(`tests/js/test_offline_audit.js` holds the line).

It also briefly excluded `reason: 'scripts'`, on the reasoning that
warming fetched only HTML. It has not fetched only HTML since the commit
that introduced the gate — `_warmShellSubresources` fetches the modules
the page names and the cache is missing — so the exclusion left the one
state the repair was built for with no way to reach the repair. Every
failing state is offered the control; the re-run afterwards is what says
whether it worked.

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
| Storage that hangs rather than rejecting — the bounds, the latch, the throw | `tests/js/test_offline_audit.js` ("a device whose storage stops answering") |
| Both hosts' markup, the strings-template drift check, the precache | `tests/accounts/test_settings_offline_audit.py` |

No Playwright test. Everything here is either arithmetic or a DOM
assertion jsdom can make, and `tests/e2e/` is a dozen smoke tests
([client-side-tests.md](client-side-tests.md)).
