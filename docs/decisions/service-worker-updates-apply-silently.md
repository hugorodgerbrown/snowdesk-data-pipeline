---
name: service-worker-updates-apply-silently
description: Why a waiting SW is applied silently on hide and the banner is only for a stuck worker — applyWaitingWorker, workerIsStuck, shell-identity
status: current
last-reviewed: 2026-09-24
---

# Service-worker updates apply silently; the banner is for a stuck worker

**Decision.** A deploy asks nothing of the user (SNOW-1025).

* **Routine update: silent.** `sw.js` still never calls `skipWaiting()`
  on install. Instead, `sw_register.js` records the waiting worker
  (`queueSilentUpdate`) and posts `SKIP_WAITING` the next time
  `document.visibilityState` becomes `hidden` (`applyWaitingWorker`). It
  holds back while any `warmCache` run is queued or in flight. The
  `controllerchange` that follows does **not** reload the page.
* **Stuck worker: the banner.** `window.pwaUpdateBanner.reveal()` shows
  the banner only when both gates agree. Two things ask it: an installing
  worker that goes `redundant` without ever reaching `installed`
  (`watchForInstall`), and `pwa_version_check.js` after a confirmed version
  drift. The first matters most. After a deploy, the first navigation
  already carries the new build's meta, so a failed install produces no
  drift. The gates are: `shellIsStale` (SNOW-952: the
  controller's `CACHE_VERSION` differs from `shell` on `/api/version`)
  and `workerIsStuck` (after `registration.update()`, nothing is waiting
  and the installing worker, if any, went `redundant` or timed out).
* **Plain copy.** "Snowdesk needs a refresh / It couldn't update itself.
  Reload to finish." No build SHAs, no release labels.
* **No build in the worker.** `BUILD_IDENTITY`, `inject_build_identity`
  and check `core.sw_cache_version.E003` are gone. The worker answers
  `shell-identity` with its `CACHE_VERSION` only.

This supersedes the "one Reload message per update" contract from SNOW-331,
[`the-update-banner-names-the-worker-being-replaced.md`](the-update-banner-names-the-worker-being-replaced.md)
(SNOW-933), and narrows
[`the-update-banner-is-gated-on-the-shell-not-the-build.md`](the-update-banner-is-gated-on-the-shell-not-the-build.md)
(SNOW-952) to the first of two gates.

## Why

The banner exists as an escape hatch for a worker that cannot update.
`install` precaches with one atomic `cache.addAll`, so one bad entry
rejects it and no worker ever reaches "waiting". An escape hatch only works
if it is rare enough to be read.

SNOW-952's shell gate was the right question for spotting a stale device,
but it answers yes after every deploy that touches a shell source. The
shell hash covers every `static/js/*.js`, `src/css/main.css` and the shell
templates, so that is nearly every feature deploy. Because a waiting worker
could only be activated by a click, "stale" and "needs the user" were the
same thing, so the banner kept showing up: "Update available (3de6af2) —
You are on 5f20005".

A routine update needs no one. HTML is network-first and static assets are
hashed, so an online page is already current. The only thing the waiting
worker changes is the offline shell, and it can swap that in unseen.

**Why hidden, not immediately.** Activating under a visible page swaps
the worker that page is talking to, and the activate sweep deletes the old
shell cache the page may still read from. Hidden means the user has
switched away (tab, app, screen lock), which on an installed PWA happens
many times a day. Waiting for every tab to close, the browser's default,
can take days on an installed app.

**Why not reload after the silent activation.** A reload in a background
tab still loses whatever the user left there: a half-written trip or field
report, a scrolled panel. The next navigation lands on the new shell
anyway.

**Why no versions in the copy.** The banner now means "you are stuck", and
what a stuck person needs is the action. Naming builds also cost a
per-deploy value baked into `sw.js`, which made the worker's bytes differ on
every deploy, Python-only ones included. Without it, the worker changes
when, and only when, the shell does.

## Consequences

* **`pwa.sw.update_available` now counts stuck-worker banners**, once per
  page. `pwa.sw.update_applied` fires for silent activations too.
* **An open page can run old JavaScript against a new worker** until its
  next navigation. The two stay compatible as long as the worker's message
  contract only ever grows. A removed message type falls back to its
  caller's bounded timeout, as `shell-identity` replacing `build-identity`
  does for one deploy.
* **The memoised banner answer is cleared on `controllerchange`.**
  SNOW-952 could latch it per server shell, because the controller changed
  only by a reload. That is no longer true.
* **The first deploy carrying this** still has the old worker in control:
  that worker never answers `shell-identity`, so gate one reads "unknown",
  which is stale. Gate two then finds the new worker installing or waiting,
  so no banner appears.
* **The release label is still sent** (`release` on `/api/version`,
  `<meta name="pwa-app-release">`) but has no client reader. Removing it is
  follow-up work.
