---
name: what-this-device-holds-is-a-public-page
description: /offline/, offline_page, PUBLIC_PRINCIPAL_PATHS, SHELL_PAGES — the offline report, reset control and sync log are public, not settings
status: current
last-reviewed: 2026-09-13
---

# What this device holds is a public page, not an account setting

**Decision.** The offline-content audit, the reset-local-data control with
its size breakdown, and the sync log live at **`/offline/`**
(`apps.public.views.offline_page`), which needs no account. Theme stays on
`/account/settings/`, which keeps a link where the block used to be.

## Why

All three were under the "This device" eyebrow on `/account/settings/`,
which redirects an unauthenticated visitor straight to sign-in. Not one of
them touches an account: every reading is computed client-side from Cache
Storage, IndexedDB and `navigator.storage.estimate()`. They were gated for
one reason — settings was the only page available to put them in.

That is the same accident SNOW-921 corrected when it moved the "Offline
mode" switch out of the account dropdown, where a device preference had
quietly become an account feature because the dropdown was the only menu
there was.

Put sharply: **the page you need when you have no signal was behind a
login.** A reader who never made an account, or whose session had expired,
had no way to ask what this device was holding — and the report is most
worth reading exactly when something has already gone wrong.

Theme is the one thing that did not move. It is a display preference with
no relationship to the network, and taking it would make `/offline/` a
second settings page rather than one answer to one question.

## Two things fall out of it, and both are the point

**It can be warmed into the shell.** `SHELL_PAGES` (`static/js/sw.js`) is
what the activation re-warms, and `/offline/` is now in it beside the map
page. A login-gated page could never have been: the warm would have
fetched a redirect to sign-in. A page that cannot itself open offline is a
poor place to explain why nothing else can.

**One cached copy serves every reader — but not for free.** `base.html`
renders `<meta name="pwa-user-id">` on *every* page, so the worker stamps
this one with the reader's principal like any other. A copy cached while
signed in stops matching after a sign-out (`_currentPrincipal()` answers
`PRINCIPAL_ANONYMOUS`, the stamp is a uuid), and `_networkFirstFallback`
would serve `static/offline.html`'s fallback instead of the page — while
offline, which is the one moment the page exists for.

The exemption is explicit: **`PUBLIC_PRINCIPAL_PATHS`**, a frozen list of
paths whose cached navigation matches any current principal, checked in
`_networkFirstFallback` beside `_principalMatches`. It mirrors
`_POSTHOG_EXEMPT_PATHS` in `config/settings/base.py` — a small, readable,
one-place list of paths that are public by construction. Matched on the
pathname exactly, so a query string cannot smuggle a non-public page past
it.

Two alternatives were considered and are worse:

- **A response header the view sets.** Fails in the wrong direction: a
  copy cached before that header shipped is indistinguishable from an
  account page, so every existing entry would stay refused.
- **A sentinel principal value.** Would need every existing comparison
  rewritten, for one page.

The constant is versioned with the worker doing the matching, which is the
property that matters.

## What did not change

`static/offline.html` is untouched as the zero-server fallback the worker
serves for a navigation to a page never visited. It keeps its own inlined
copies of the audit and the reset, because a device whose worker has not
activated since this shipped still may not hold `/offline/`. What it gains
is a link, for a reader who does have a connection.

Page metadata is `sharing=False`: a per-device diagnostic has nothing to
unfurl. Its content is the reader's own storage, so a link card would
describe a reading the recipient's device does not have and cannot be
given. Not a privacy exemption — simply a page with no shareable subject.

## Related

- [`the-shell-is-rewarmed-after-an-activation.md`](the-shell-is-rewarmed-after-an-activation.md)
  — why `activate` re-warms at all, and where `SHELL_PAGES` came from.
- [`the-way-out-of-offline-mode-is-on-the-page-it-strands-you-on.md`](the-way-out-of-offline-mode-is-on-the-page-it-strands-you-on.md)
  — the same instinct one page over: the control belongs where the person
  who needs it can reach it.
- [`account-area-navigation-lives-in-the-nav-menu.md`](account-area-navigation-lives-in-the-nav-menu.md)
  — the account area is one page, and this ticket is part of why it stayed
  one.
- [`docs/offline-audit.md`](../offline-audit.md) — what the report asks and
  how it answers.
