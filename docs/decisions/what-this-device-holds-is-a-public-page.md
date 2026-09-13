---
name: what-this-device-holds-is-a-public-page
description: /offline/, offline_page, SHELL_PAGES, pwa-user-id — the offline report, reset control and sync log are public, not settings
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

**One cached copy does NOT serve every reader, and that was the first
answer.** The first cut exempted `/offline/` from the principal check so a
single cached copy would serve anybody. The review of #909 rejected it, and
the reasoning is worth keeping because the mistake is an easy one to make
again: **being a public page and being an identity-neutral document are
different things.**

`base.html` renders `<meta name="pwa-user-id">` on *every* page, so the
cached document carries the principal it was rendered for, and page-side
code reads it. `mutation_queue.js` runs on every public page and
`_reconcilePrincipal()` trusts that meta. Serving account A's copy to
account B on a shared browser would have cleared B's queued mutations
*and* rewritten `mutations.principal` to A — after which every mutation B
made was stamped A and discarded at the next drain. Silent data loss,
offline, on the one page a stuck reader is told to open.

Removing the meta from the page does not fix it. An absent tag makes
`_currentPrincipal()` answer `null`, which is a real value meaning
**anonymous** — so the page would then clear a *signed-in* reader's queue
instead. Making the copy genuinely identity-neutral means giving a page a
way to say "do not reconcile against me" and teaching every reader of
`pwa-user-id` to honour it: a wider change than the property it buys, and
one that widens a surface built to protect queued writes.

So the page is partitioned like any other. The cost is that a *second*
reader of the same browser does not get the cached copy and falls through
to `static/offline.html`, which carries its own inlined audit and reset for
exactly that case. Everything else this decision is for is untouched: the
page is public, it needs no login, and it is warmed into the shell for
whoever is signed in when the worker activates.

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
