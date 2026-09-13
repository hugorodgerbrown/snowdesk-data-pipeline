---
name: the-update-banner-is-gated-on-the-shell-not-the-build
description: Why the update banner compares CACHE_VERSION not APP_VERSION — shellIsStale, the /api/version shell field, build-identity cache, revealNow
status: current
last-reviewed: 2026-09-13
---

# The update banner is gated on the shell, not the build

**Decision.** `#sw-update-banner` is revealed only when the shell cache
name the **controlling service worker** holds differs from the one the
server would serve today. The worker reports its `CACHE_VERSION` in the
`build-identity` reply it already answers; the server reports the same
value as `shell` on `/api/version`; `shellIsStale()` in
`static/js/sw_register.js` compares them, and
`window.pwaUpdateBanner.reveal()` is that gate. Both reveal paths — the
waiting-worker path in `sw_register.js` and the header-drift path in
`pwa_version_check.js` — go through it, so the predicate lives in one
place. `revealNow` is the ungated DOM primitive underneath, for the tests
that exercise the banner's copy rather than its trigger.

The build identifiers are untouched: `BUILD_IDENTITY` still names the
deploy, and the banner still says "You are on v29. Reload to update to
v30." A build names *what the banner says*; the shell decides *whether it
says anything*.

## Why

The banner is the escape hatch for a service worker that has got stuck.
`install` runs one atomic `cache.addAll(PRECACHE_URLS)`, so a single bad
entry rejects it and no worker ever reaches `waiting` — the SW path is
silent for as long as the fault lasts, and the header-drift path is the
only signal a normal user gets. Clicking Reload there clears the shell
caches, which is the cure.

An escape hatch has to be rare enough to be read. This one was firing on
every deploy, in both paths, for reasons that had nothing to do with the
device in front of the user:

* `update_available` on `/api/version` is
  `client_version != settings.APP_VERSION` — "the server has redeployed".
  Any tab open across any deploy trips it. A device whose worker has been
  failing to install for three days trips it identically, and that second
  case is the one the banner exists for.
* SNOW-933 began injecting `APP_VERSION` into the worker body, and the
  ETag is computed over the final substituted body — so the served
  `sw.js` bytes differ on every deploy, including one that touches only
  Python. A replacement worker installs and parks on every user's first
  load. When the shell hash has not changed, activating it deletes
  nothing (`activate` sweeps caches whose name `!== CACHE_VERSION`) and
  re-adds the same three precache URLs. The reload's entire effect is
  swapping one worker for another that behaves identically.

Neither is a statement about the client. An online client does not need
the banner to become current at all: HTML is network-first, and static
assets are hashed by `CompressedManifestStaticFilesStorage`, so a changed
file is a new URL and therefore a cache miss that goes to the network.
The thing a reload can actually fix is the shell this device opens
**without** a network — and the value that tracks it is `CACHE_VERSION`,
derived from the shell content hash (`apps/core/sw_shell.py`), which
changes when and only when a shell source does.

The contract `sw_register.js` opens with — *if there is no message, you
are already on the latest version* — only holds with the gate. Its
converse only means anything if the message is rare.

## Why not CACHE_VERSION everywhere, or APP_VERSION everywhere

Because they name different things, and each is right for one job.
`APP_VERSION` names a **deploy** and is the only identity that can be
rendered as "v29 → v30", which is what
[`the-update-banner-names-the-worker-being-replaced.md`](the-update-banner-names-the-worker-being-replaced.md)
is about. `CACHE_VERSION` names a **shell** and is the only identity that
answers "has anything this device holds changed". Using the build for
both is what this decision reverses; using the cache name for both would
put a twelve-character content hash in front of a reader.

They cannot be collapsed. The controlling worker's build cannot be
recovered from its cache version — no map runs backwards from a shell
hash to the deploy that served it — so both values ride in the one
`build-identity` reply, and are read from the one round trip.

## Failing open, and the one case that fails closed

Three answers, not two:

* **No controller** → not stale. Nothing is cached, so nothing can be
  out of date, and the page in front of the user came off the network.
  This is the one unknown that must not reveal.
* **A controller that cannot be read** — no `shell` from the server, no
  reply inside the 2s budget, or a worker predating this change that
  replies without a `cache` field → **stale**. An unanswering worker is
  itself a symptom of the state the hatch exists for, and revealing is
  what happened before this change, so the unknown fails toward today's
  behaviour rather than away from it.
* **Both readable** → the comparison.

Note this is the opposite direction from `labelBanner`'s fallback, which
declines to name builds it could not verify. The distinction is that
naming the wrong build misinforms, while offering a reload nobody needed
merely wastes one click — and withholding the hatch from someone who is
stuck costs them the app.

Like every change to the worker's message contract, the first deploy
carrying this shows the banner to everyone: the worker being replaced
predates the `cache` field, does not report one, and the unknown reveals.
It self-corrects on the next deploy.

## Consequences

* **A worker still installs and parks on every deploy**, because
  `BUILD_IDENTITY` still changes the worker's bytes. It is now invisible
  and costs one `cache.addAll` of three URLs. Removing the churn would
  mean dropping the injection, which would cost the banner its readable
  copy — the trade this decision declines to make.
* **`pwa.sw.update_available` moved behind the gate.** It counts what a
  user was offered; in front of the gate it would count deploys, and the
  dashboards would disagree with what anyone saw.
* **The server half must be as fresh as the event that raised the
  question.** `pwa_version_check.js` verifies a header drift once per
  distinct header value and then holds that body, so a tab that confirmed
  a build-only deploy B keeps handing B's body back for every replay of
  B's header. The waiting-worker path is woken by something else entirely
  — a worker from deploy C installing — and judging it against B's body
  compares C's worker against B's shell; where B changed no shell source
  the two match, and the only notification C would ever produce is
  swallowed. So `showUpdateBanner` passes `refresh: true` and
  `verified()` goes back to the network, sharing any fetch in flight. The
  header-drift path passes nothing: its body was fetched by the
  verification that raised its question moments earlier.
* **The answer is memoised against the server shell it answered for**,
  not latched. `pwa_version_check.js` re-offers the banner for every
  response replaying a drifting version header, so an unmemoised gate
  would message the worker on each of them; a latch would deny a second
  deploy in one session its own answer.
* **`shell` must be read the same way on both sides.** `serve_sw`
  recomputes under `DEBUG` and uses the per-process cache otherwise, and
  `version()` mirrors that exactly. If the two could disagree the banner
  would either never appear or never stop, and both failures are silent.
  `tests/public/test_pwa_version_api.py` pins the API's value to the one
  in the served worker.
