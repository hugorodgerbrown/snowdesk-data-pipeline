---
name: the-update-banner-names-the-worker-being-replaced
description: Why the update banner's build comes from the controlling worker, not pwa-app-version — BUILD_IDENTITY, build-identity, controllerIdentity
status: current
last-reviewed: 2026-09-12
---

# The update banner names the worker being replaced

**Decision.** The build the update banner calls the user's is the one the
**controlling service worker** was served from — read by posting
`{type: 'build-identity'}` to `navigator.serviceWorker.controller` and
waiting a bounded 2s for the reply (`controllerIdentity` in
`static/js/sw_register.js`). The worker answers from `BUILD_IDENTITY`, a
placeholder in `static/js/sw.js` that `apps.public.views.serve_sw`
rewrites per response with `settings.APP_VERSION` and `release_label()`,
alongside the `CACHE_VERSION` substitution it already performed.

The page's `<meta name="pwa-app-version">` is the **fallback**, used only
when no worker answers. Both reveal paths — the SW-update path in
`sw_register.js` and the header-drift path in `pwa_version_check.js` —
use the same rule.

The worker's answer is taken **whole**: both its build and its release
label, or neither. Pairing one build's SHA with the other's label would
produce a sentence in which the two halves describe different things.

## Why

SNOW-869 gave the banner copy that names both builds — release labels
when they differ, short git SHAs when they don't — and on staging it
never once appeared. Every staging deploy sits inside the same release,
so the labels always matched and the rule fell through to the SHAs; the
SHAs matched too, and the banner kept the unnumbered "Update available"
for its entire life.

The reason is that the page is not the shell. HTML navigations are
network-first (`_networkFirst` in `static/js/sw.js`), so the first
navigation after a deploy fetches the **new** HTML — carrying the new
build's `<meta>` — while the **old** worker is still controlling the
page. The new `sw.js` installs, waits, and raises the banner. At that
moment the page's build and the server's build are the same string, and
the only thing that is actually out of date is the worker, whose identity
appeared nowhere on the page.

So the comparison was one identity short. It compared the document
against the server, when the thing being replaced is the worker.

Production has the same gap with the labels: a fresh `v34` page
controlled by a `v33` worker also compares equal, so the release labels
the copy was written for never showed there either.

## Why a second constant rather than CACHE_VERSION

`CACHE_VERSION` is already per-build and already injected, and the worker
could have reported it. It names a **cache**, derived from the shell
content hash (SNOW-590 — see `apps/core/sw_shell.py`), and the
server's half of the comparison is a git SHA from `/api/version`. Two
strings of different kinds cannot be put either side of "You are on X.
Reload to update to Y" without the sentence lying about one of them.
`BUILD_IDENTITY` names a **deploy**, which is the same thing
`/api/version` names.

## Consequences

* **The first deploy carrying this change still shows the unnumbered
  banner.** The worker being replaced predates the `build-identity`
  handler, does not answer, and the page meta fallback reproduces
  today's behaviour exactly. It self-corrects on the next deploy. This
  is a property of any change to the worker's message contract, not a
  defect in this one.
* **A failed substitution is loud.** `inject_build_identity()` raises,
  and `apps.core.checks.check_sw_cache_version_substitutable` probes it
  at `manage.py check` time — the same treatment `CACHE_VERSION` gets,
  for a lesser failure: an unsubstituted placeholder would reach a user
  as "You are on UNSUBST", which reads as a broken app rather than as an
  available update.
* **The read is bounded and never throws.** A worker that cannot answer
  resolves `null` after 2s, per
  [`bounded-offline-read-paths.md`](bounded-offline-read-paths.md) — the
  caller is holding on-screen copy open while it waits, so it must get
  an answer rather than a hang.
* `describeUpdate()` is unchanged. It was always correct; it was being
  fed the wrong pair.
