---
name: blocked-builds-not-a-version-floor
description: APP_BLOCKED_VERSIONS names the builds the server refuses; no APP_MIN_VERSION floor, because APP_VERSION is a git SHA and SHAs have no order
status: current
last-reviewed: 2026-08-04
---

# Blocked builds, not a version floor

**Decision.** The forced-update gate is a **membership test against an
explicit set**, decided server-side. `settings.APP_BLOCKED_VERSIONS` (a
comma-separated env value) names the builds the server refuses to keep
serving; `/api/version` returns `update_required: true` when the request's
`X-Client-Version` is a member. There is no minimum-supported-version
setting, no `min_supported` field, and no `X-App-Min-Version` header. The
client performs no version comparison of any kind — it branches on the
boolean.

The verdict **fails open on an unidentified client**: no
`X-Client-Version` header means no block, ever.

**Why.** `APP_VERSION` resolves to `RELEASE_VERSION`, which on Render is
`RENDER_GIT_COMMIT` — a git SHA. SHAs have no order. A "minimum supported
version" is therefore not expressible on either side of the wire: the
server cannot ask "is this client's SHA below mine?" any more than the
client can. The previous implementation papered over that with string
inequality (`differs()`), which answered "different" for every client
including one on the newest build, so setting `APP_MIN_VERSION` to any
value would have swept the entire population into a wipe-and-reload loop.
It was dormant only because the setting was never set (finding M4,
`docs/code-reviews/2026-08-03-js-review.md`).

Moving the same comparison to the server would not have helped — the
server cannot order SHAs either. What ops actually needs to express is not
"everything before X" but "these specific builds have a bug we cannot live
with", which is a set. Naming builds explicitly also matches how a block is
issued in practice: deploy the replacement, then list the build being
retired.

Failing open is the asymmetry that matters. A missed block leaves a client
on a stale build until it next reloads. A wrong block puts an
un-dismissable modal in front of someone with no way out — the modal's only
control triggers a reload that lands them on the same unidentified state.

**Consequences.**

- Blocking a build is an env-var change (`APP_BLOCKED_VERSIONS`) on the
  Render dashboard, not a deploy. The value should list SHAs, not
  ranges.
- Blocking the build the server is *itself* serving does nothing: the
  verdict is only read after an `X-App-Version` drift schedules the
  `/api/version` round trip. Deploy first, then block.
- `/api/version` now varies on `X-Client-Version` — its body depends on a
  request header, so a shared cache must not replay one client's verdict
  to another. The endpoint stays `public, max-age=60`; the cache is
  partitioned per build, which is low cardinality.
- Old shells are disarmed rather than trusted. They read a missing
  `min_supported` / `X-App-Min-Version` as "no floor enforced", so
  removing both from the payload and the response is what makes the
  buggy comparison unreachable in clients we cannot update. Neither may
  be reintroduced under those names.
- A stale `APP_MIN_VERSION` left set in a Render environment group is
  inert, but should be deleted so it does not read as live
  configuration.
- The forced update clears the shell caches; it does **not** unregister the
  service worker. That is right for the common case — a blocked page build
  is replaced once its shell cache is gone — but it means blocking a build
  will not replace a faulty `sw.js`, because the existing worker keeps
  controlling the page. A bug inside the worker itself is what the
  Mechanism-A kill switch (`SW_KILL` / `SW_URL`, spec §6.4) is for; reach
  for that, not for `APP_BLOCKED_VERSIONS`.

**Related.** The wipe that a confirmed block triggers is scoped to the
shell caches, not to all local data — see the SNOW-609 comment block on
`clearShellAndReload` in `static/js/pwa_version_check.js`, and the
matching trade already made for the kill switch in
`static/js/sw_register.js`.
