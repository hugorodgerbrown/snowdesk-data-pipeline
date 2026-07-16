/*
 * static/js/sw-kill.js — Kill-switch service worker (SNOW-373, spec §6.3).
 *
 * This is Mechanism B of the two-mechanism kill switch in the offline-first
 * PWA spec. Mechanism A is the ``/api/sw-config`` toggle read by
 * ``sw_register.js`` before it ever registers a real SW: flip
 * ``SW_KILL=true`` server-side and the client unregisters on next launch.
 *
 * This file is Mechanism B: a pre-tested worker that runs when Mechanism A
 * can't fire — because the real ``/sw.js`` shipped a bug that broke the
 * page enough that ``sw_register.js`` never ran, or an entire cohort is
 * stuck on a version whose registration code doesn't check
 * ``/api/sw-config``. Ops points ``SW_URL=/sw-kill.js`` (or edits Render
 * env), and every installed client that fetches the SW at
 * ``/sw.js`` (via the existing ``updateViaCache: 'none'`` register call
 * from the old script) or via a fresh registration through the updated
 * ``sw_register.js`` will register THIS worker instead of the real one.
 *
 * On ``activate`` this worker:
 *   1. Deletes every Cache Storage entry — the site's shell caches, plus
 *      any third-party caches an earlier version stored under this
 *      origin.
 *   2. Deletes every IndexedDB database — spec §6.3 Decision is to wipe
 *      by default. Snowdesk has no critical unsynced client-side writes
 *      that would warrant the sync-then-wipe variant.
 *   3. Unregisters itself — the next navigation is served straight from
 *      the network with no SW in the way.
 *   4. Reloads every open tab so the user sees a working page on the
 *      spot, without having to close and reopen.
 *
 * Deliberately no ``fetch`` handler. Absence of a fetch listener means
 * the browser bypasses the SW entirely for network requests, even before
 * this worker calls ``unregister()`` — so the moment this file activates,
 * the user's page traffic goes straight to the network. This is the
 * whole point: whatever the real SW was doing wrong, this one gets out
 * of the way immediately.
 *
 * i18n: no user-visible strings — the reload is silent.
 */

'use strict';

self.addEventListener('install', (event) => {
  // Take over immediately: don't sit in the "waiting" state the real SW
  // uses to gate updates behind a Reload button. When the kill switch is
  // deployed the user needs it live now, not after they click something.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // 1. Wipe Cache Storage.
      const cacheNames = await caches.keys();
      await Promise.all(cacheNames.map((name) => caches.delete(name)));

      // 2. Wipe IndexedDB. ``indexedDB.databases()`` is not on Safari
      // < 14, so fall back to a no-op there — a subsequent shipped SW
      // can enumerate known DB names by hand if it needs them gone.
      // The Promise wraps each deletion so ``onblocked`` (fires when a
      // still-open connection prevents delete) resolves rather than
      // hanging the activate handler indefinitely.
      if (indexedDB.databases) {
        const dbs = await indexedDB.databases();
        await Promise.all(
          dbs
            .filter((db) => !!db.name)
            .map(
              (db) =>
                new Promise((resolve) => {
                  const req = indexedDB.deleteDatabase(db.name);
                  req.onsuccess = () => resolve();
                  req.onerror = () => resolve();
                  req.onblocked = () => resolve();
                }),
            ),
        );
      }

      // 3. Unregister ourselves. From this point on, new navigations go
      // straight to the network — the browser has no controller for the
      // origin's scope.
      await self.registration.unregister();

      // 4. Reload every open tab so the user sees the fresh page without
      // needing to close and reopen. ``client.navigate`` is safer than a
      // full ``location.reload`` message dance — it works from the SW
      // context and re-fetches the current URL.
      const clients = await self.clients.matchAll({ type: 'window' });
      // SNOW-384: best-effort SW → page telemetry, posted just ahead of
      // the navigate() calls below so the still-open old page has a
      // chance to run window.pwaTelemetry.emit() (critical event →
      // sendBeacon) before its own navigation tears it down. There is no
      // synchronous alternative from a SW context; some loss under a
      // fast navigate race is accepted, same as other reload-adjacent
      // telemetry in this codebase.
      clients.forEach((client) => {
        try {
          client.postMessage({
            type: 'pwa-telemetry',
            event: 'pwa.kill_switch.activated',
            properties: { mechanism: 'b' },
          });
        } catch (_err) {
          // Ignore.
        }
      });
      await Promise.all(
        clients.map((client) => {
          try {
            return client.navigate(client.url);
          } catch (_err) {
            return null;
          }
        }),
      );
    })(),
  );
});

// Deliberately NO fetch handler here — see the header comment.
