---
name: the-shell-is-rewarmed-after-an-activation
description: sw.js activate re-warms the map page and its scripts/styles — _rewarmShell, _shellSubresources, SHELL_PAGE; a deploy broke offline open
status: current
last-reviewed: 2026-09-11
---

# The shell is re-warmed after an activation

**Decision** (SNOW-912). `activate` puts the map page back in the shell
cache it has just emptied. `_rewarmShell()` runs after `clients.claim()`, inside the same
`waitUntil`, and warms `SHELL_PAGE` (`/`) through `_warmCache` — which, for a
same-origin HTML response, now also fetches the page's own same-origin
scripts and stylesheets (`_shellSubresources`, capped at
`SHELL_SUBRESOURCE_LIMIT`, cache-checked before each fetch).

It is bounded by the same offline mode every other network path in this file
consults: a device the user has switched offline, or one that has latched
there, spends nothing. It never throws — an activation that fails leaves the
device with no worker at all, and this is a convenience, not a precondition.

## Why

`activate` deletes every shell cache that is not the version now live, which
is correct and load-bearing: stale HTML pointing at hashed assets that no
longer exist is worse than no HTML at all. What nothing did afterwards was
put the page back. From the moment a deploy activated until the user next
opened `/` **while connected**, the app could not open offline at all.

That gap was silent, it opened on **every** deploy, and no surface reported
it until SNOW-907 shipped the offline-content report — which is how it was
found. A device nine minutes past a deploy, on a train with a working
signal, reported "The app opens: No". The report was right. The user had
been on `/account/settings/` when the new worker activated, so the only page
re-cached was the one they were reading.

The page alone is not enough, and that is the second half of the decision. A
page whose HTML is saved and whose JavaScript is not does not open — it
paints a blank frame, which to the person holding the phone is
indistinguishable from a page that was never saved. Warming the HTML on its
own would have moved the report's "The app opens" row to Yes while leaving
the user with exactly the failure that row exists to catch.

Activation was chosen over the two alternatives. Precaching on **install**
runs against the old worker's cache generation, so the page would have to be
re-fetched at activation anyway. Warming **from the page**, after
`controllerchange`, only ever reaches tabs that happen to be open at update
time — and the device that most needs the shell is the one whose owner
updated the app at home and opened it on a lift.

## Consequences

- Every activation costs one page fetch plus whatever that page references
  and the cache does not already hold. On a device that has simply opened
  the app, the subresource pass is one `cache.match` per entry and no
  network at all; the fetches happen on the one occasion they are the point,
  the generation after a deploy.
- The warmed page is stamped with the principal its HTML declares
  (SNOW-624), so it is servable to whoever was signed in when the worker
  activated — and refused, correctly, after a sign-out.
- `_shellSubresources` matches same-origin `.js`/`.css` by URL, not by tag.
  A future shell asset in another form — a module graph reached only through
  `import`, a font the layout blocks on — will not be warmed by this and
  needs adding deliberately.
- The report's "Save the map page" control remains the manual path for a
  device that missed the warm (offline at activation, or a partial run).
- `activate` stops waiting after `SHELL_REWARM_BUDGET_MS`. The bound is on
  this caller rather than on `_warmCache`'s fetches, which must stay
  unbounded for a several-thousand-tile basemap download; a radio that
  hangs rather than rejecting would otherwise hold the activation open.
