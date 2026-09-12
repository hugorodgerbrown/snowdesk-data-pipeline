---
name: the-way-out-of-offline-mode-is-on-the-page-it-strands-you-on
description: Offline mode switch on static/offline.html as well as nav — pwa_network_mode.js, canOpenOffline, network-use audit row, no self-heal
status: current
last-reviewed: 2026-09-12
---

# The way out of Offline mode is on the page Offline mode strands you on

SNOW-922.

**Decision.** The "Offline mode" switch appears in two places: the account
menu (`includes/nav.html`, as before) and the offline fallback page
(`static/offline.html`, new). Both work the same mechanism —
`static/js/pwa_network_mode.js`, precached in `PRECACHE_URLS` — and both
guard the ON direction by asking the service worker, over a
`can-open-offline` message, whether the app would in fact open with no
network. The worker **never** leaves a forced mode by itself.

## The trap

`'offline-forced'` is the user's standing instruction not to spend their
connection, and `sw.js` keeps that promise completely. `_shouldUseNetwork()`
answers false on every read path, HTML navigations included, so
`_networkFirst` goes straight to `_networkFirstFallback`. That is correct
and it is deliberately watertight —
`tests/offline/test_offline_toggle_is_watertight.py` exists to hold it, and
its assertion is zero requests.

The mode is persisted to `meta:app` (`network.mode`) and re-hydrated by
`_hydrateNetworkMode()` on every worker boot, so it survives the tab
closing, Chrome recycling an idle worker after ~30s, and the device
restarting.

On a device whose map page is cached for the account signed in, all of that
is exactly right. On one where it is **not**, every navigation falls through
to `offline.html` and stays there. Four ordinary ways to be in that state:

- a fresh install, where the switch was found before the map was opened;
- a sign-in as someone else — `_networkFirstFallback` refuses a shell entry
  whose `X-SW-Principal` does not match, and says so nowhere;
- the window after a deploy, before `activate`'s `_rewarmShell` has put the
  map page back (see
  [`the-shell-is-rewarmed-after-an-activation`](the-shell-is-rewarmed-after-an-activation.md));
- Cache Storage evicted under pressure.

A live signal made no difference, because it is the worker refusing and not
the radio — so the page's own "Reconnect and try this page again" was advice
for something that could not work. The only control that ended the state
lived in the account menu, inside the app that would not open: **the exit was
behind the door it locks.** What remained was "Reset local data", which also
destroys every downloaded region, saved place and queued mutation, or
clearing site data in the browser's own settings. Reported from staging by
someone holding a phone with a 200 MB Martigny-Verbier download on it and no
way to see any of it.

## Why the worker does not self-heal

The obvious fix — notice that no shell page is cached, conclude the mode is
pointless, and drop it — was considered and declined.

A forced mode has no evidence to expire. It is not an inference the worker
drew and can revise, the way the auto-`'offline'` latch is; it is a person
saying "do not spend this connection", usually because it costs money or
battery. A worker that quietly resumed calling the server on that person's
roaming data would be breaking the one promise this feature makes, and it
would break it precisely when they cannot see that it has. The watertight
test above would have to be relaxed to allow it, and that test is what every
other test in `tests/offline/` rests on.

So the mode ends when the user ends it, and the fix is to make sure they
always can.

## Why a module rather than a second copy

`pwa_offline.js` already knew how to change the mode, but the recovery page
cannot use it: that module binds to nav markup this page does not have and
reads `window.pwaDb`, which it does not load. Restating the rule inline
would be the wrong answer, because the rule is exactly the thing that must
not drift:

- a page that **announces without persisting** is re-stranded by the next
  worker restart, which is the original bug;
- a page that **persists without announcing** leaves the live worker in the
  old mode until it is recycled.

`pwa_network_mode.js` owns that pair, in that order, with two callers. Its
storage is an adapter rather than a choice: `window.pwaDb` where the app has
loaded it (so an app-side write still goes through the layer that owns the
schema version, the upgrade path and the Reset-Required state), a direct
**versionless** open where it has not. Versionless is load-bearing — it never
requests an upgrade, so the recovery page cannot migrate a schema it does not
know the shape of.

## Why the guard asks the worker

`canOpenOffline()` is a `can-open-offline` message, answered by `sw.js`'s
`_canOpenOffline()`. The question is precisely "would `_networkFirstFallback`
find a page for a navigation to `/`", and every input — the live shell cache,
the entry, its principal stamp, the account signed in now — is already in that
file. A page-side copy would be a second answer to a question that has one, and
the two would drift the first time either side of the principal rule changed.

It answers false on every doubt: no controller, no reply inside its budget,
an unrecognised reply. It gates a **warning**, so a false costs one extra
confirmation press and a wrong true costs someone the app.

The warning names the consequence and does not overrule anyone — someone who
genuinely wants aeroplane mode on a fresh device can still have it.

## The report had the reading and threw it away

`offline_audit.js` collected `readings.networkMode` from the day SNOW-907
shipped, and `offline_audit_core.js` consumed it nowhere. Two consequences,
both visible in the staging report that prompted this:

- **The mode had no row**, so the one state that makes every other row's
  remedy unreachable was invisible, and the verdict read "The app will not
  open without a signal. Open the map once while connected" — the single
  action this mode makes impossible.
- **The row above it was misread.** It was labelled "Offline mode is on" and
  answers from `serviceWorker.controlled`, which is a different thing
  entirely; its green Yes read as confirmation of the switch.

So the old row is `offline-support` ("Offline support is installed") and the
switch has its own row, `network-use` ("The app may use the network"). That
row is deliberately **not** `critical`: a forced mode on a device whose app is
saved is working exactly as asked. It is a fault only in combination, and
`verdictFor` is where the two meet — `verdict-forced-lockout` fires only when
`app-opens` is blocked **and** the mode is forced, and it is checked before
`verdict-no-page` so the impossible advice is never given.

A merely latched worker (`'offline'`) never gets that verdict. The latch lifts
itself when a probe finds a route, so telling the reader to press a switch
would be advice for a state that may already be gone.

## Consequences

- `pwa_network_mode.js` joins `PRECACHE_URLS` — the atomic list, where a
  failed entry fails `install`. It earns that on the same ground
  `pwa_reset.js` does, in its strongest form: the state it recovers from is
  one the worker itself creates.
- The nav switch's ON direction is now asynchronous (it awaits the worker's
  answer). OFF stays synchronous in both surfaces — that is the recovery
  direction, and nothing belongs between a stranded user and the network.
- `static/offline.html` restates the switch's geometry and the lock-out copy
  literally. It reaches no stylesheet and no message catalogue, as everything
  else on that page already does; the app-side copy is in
  `includes/_network_mode_strings.html`. **Keep the two wordings in step by
  hand.**
