---
name: dev-bypasses-the-shell-cache
description: SW_DEV_SHELL_BYPASS makes sw.js skip the shell cache in local dev so a stale worker can't serve pre-pull assets; production is unaffected
status: current
last-reviewed: 2026-08-02
---

# Dev bypasses the shell cache

**Decision.** In local development only, `static/js/sw.js`'s
`_staleWhileRevalidate` skips the shell cache entirely — no read, no write —
and goes straight to `fetch(request)`. The carve-out is gated on a named
setting, `SW_DEV_SHELL_BYPASS` (`config/settings/base.py`, default `False`;
`development.py` flips the default to `True`), substituted into the served
`/sw.js` body by `apps.public.views.serve_sw`. `apps.core.checks` errors if
it is ever `True` with `DEBUG` off, so it cannot reach production. The
update banner (`sw_register.js::showUpdateBanner`,
`pwa_version_check.js::showSoftBanner`) is suppressed whenever the bypass is
active, via a `<meta name="pwa-dev-shell-bypass">` tag rendered only in that
case. An opt-in checkbox on `/_sw-version/` (`static/js/pwa_dev_shell_toggle.js`)
restores ordinary caching for anyone who deliberately wants to exercise it
locally.

**Why.** `sw.js` deliberately never calls `skipWaiting()` — a freshly
installed worker sits in the "waiting" state until the user clicks
"Reload" on the update banner (see `sw.js`'s "Update contract"). That is
correct for production, but in local development it means the OLD worker
stays in control after every `git pull` and keeps serving the previous
`map.js` and friends out of its own `CACHE_VERSION` cache — the page looks
current, the code running it is not. The only previously-documented fix was
DevTools → "Update on reload", folklore every developer rediscovers on a
fresh browser profile.

`skipWaiting()`-in-dev was considered and rejected: the page that triggers
the install has already run with the old assets, so that trades one stale
reload for a guaranteed second, unnecessary one. Bypassing the cache read
instead fixes the failure even while the previous worker is still in
control — it is the old worker's `fetch` handler that runs, and once the
bypass ships in its bytes, every subsequent `git pull` is clean on the
first reload.

`DEBUG` alone would be the wrong gate: `tox -e e2e` runs under
`config.settings.development` (`DEBUG = True`) and
`tests/e2e/test_pwa_lifecycle_update.py` asserts the banner and
`pwa.sw.update_available` fire — both of which this bypass suppresses. The
e2e tox env therefore pins `SW_DEV_SHELL_BYPASS=false` so that suite keeps
testing production semantics, while a bare local dev server (no override)
gets the fix by default.

The page-side suppression rides a `<meta>` tag rather than the existing
async `/api/sw-config` fetch: two independent scripts reveal the banner
(`sw_register.js` and `pwa_version_check.js`), and both need to read the
flag synchronously at startup — an async fetch would force a shared
promise or an ordering dance between the two. A `<meta>` tag has direct
precedent (`pwa-app-version`, `pwa-user-id`) and leaves `/api/sw-config`
and its existing test coverage untouched.

**Consequences.** Every fresh worktree gets working local dev with no
manual "Update on reload" step. Production is unaffected: the on-disk
`sw.js` carries `DEV_SHELL_BYPASS = false` by default, the substitution
only ever runs from `serve_sw` (never `serve_sw_kill`), and the system
check makes shipping the flag turned on a failed deploy rather than a
silent regression of the shell cache everywhere.

Known cosmetic gap, deliberately not fixed here: in a tab that did not
click "Reload", `controllerchange` bails early (`!userTriggeredUpdate`)
without hiding an already-shown banner or emitting
`pwa.sw.update_applied`. The Reload button itself still works correctly.
Tracked as a separate follow-up, out of scope for this change.
