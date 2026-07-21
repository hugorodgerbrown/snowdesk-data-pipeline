"""
tests/e2e/test_pwa_db.py — Playwright tests for static/js/db.js (SNOW-375).

Covers the four contracts from the SNOW-375 scoping comment, plus the
SNOW-418 schema-v2 bump and the SNOW-482 schema-v3 bump:

1. Fresh open — all six static object stores (including SNOW-418's
   ``data:favourites`` and SNOW-482's ``log:sync``) exist at version 3.
2. Round-trip — put / get / delete / getAll / count / clear behave on
   ``queue:events``.
3. ``context()`` returns the expected eight-field envelope with correct
   types.
4. Reset Required — force a migration failure and assert
   ``isResetRequired()`` becomes true, the overlay is revealed, and the
   next ``open()`` rejects.
5. Schema upgrades — a pre-existing version-1 DB (missing
   ``data:favourites``/``log:sync``) is migrated to version 2 with
   ``data:favourites`` added; a pre-existing version-2 DB is migrated to
   version 3 with ``log:sync`` added — neither disturbs existing data.
6. ``appendSyncLog`` / ``getSyncLog`` (SNOW-482) — trims to the newest
   100 rows and reads back newest-first.

All tests navigate to ``/`` (map-as-homepage). db.js is loaded from
base.html via ``<script defer>`` — Playwright's ``domcontentloaded``
completes before deferred scripts run, so tests wait for ``load`` before
poking ``window.pwaDb``.
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Point Playwright at a pre-installed chromium binary when one is set.

    ``PLAYWRIGHT_EXECUTABLE_PATH`` is an escape hatch for environments where
    the pytest-playwright default (a versioned binary under
    ``PLAYWRIGHT_BROWSERS_PATH``) doesn't match the installed Playwright
    release. CI installs the matching binary via ``playwright install`` and
    leaves this env var unset, so this fixture is a no-op there.
    """
    executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


DB_NAME = "snowdesk-pwa-v1"
STATIC_STORES = ["queue:mutations", "queue:events", "meta:sync", "meta:app"]
# SNOW-418 (schema v2) — added alongside the four version-1 stores above.
V2_STORES = [*STATIC_STORES, "data:favourites"]
# SNOW-482 (schema v3) — added alongside the five version-2 stores above.
V3_STORES = [*V2_STORES, "log:sync"]


def _open_and_wait(page: Page, live_server_url: str) -> None:
    """Load / and wait for ``window.pwaDb`` to be defined by db.js.

    Removes ``navigator.serviceWorker`` before any page script runs so
    ``sw_register.js`` bails out on its very first line
    (``if (!('serviceWorker' in navigator)) return;``) — neither a real
    SW registration nor a ``pwa.kill_switch.activated`` mechanism-A check
    ever runs. localhost is a secure context, so without this the real
    ``sw.js`` genuinely installs and — since SNOW-384 wired
    ``pwa.sw.installed`` / ``.activated`` telemetry into it — posts its
    own events into ``queue:events`` at unpredictable real-world timing,
    which this file's ``queue:events`` count assertions don't expect.
    This file tests ``db.js`` in isolation, not the SW lifecycle, so
    removing that source of real events is the correct fix rather than
    weakening assertions.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(live_server_url)
    page.wait_for_load_state("load")
    # db.js is deferred and IIFE'd; the assignment happens synchronously
    # once the file executes but before ``load`` for defer scripts.
    page.wait_for_function("() => typeof window.pwaDb === 'object'")


def _block_map_data_feeds(page: Page) -> None:
    """Abort the map homepage's background ``/api/**`` fetches.

    The ``log:sync`` tests assert on the *exact* set of rows they write, but
    ``/`` is the map-as-homepage: map.js fires ``/api/ratings/`` plus five
    ``*.geojson`` feeds on load, and ``pwa_offline.js``'s wrapped
    ``window.fetch`` appends a ``log:sync`` row for every un-cached
    same-origin, non-static response (``.geojson`` is not a static-asset
    extension, so the feeds qualify too). Those async appends race the
    test's own writes: any that resolve *after* ``_delete_db`` land in the
    fresh DB and survive the newest-100 trim, so the surviving set is
    polluted with ``/api/ratings/`` / ``*.geojson`` rows and the assertion
    is non-deterministic. db.js loads from ``/static/`` (never ``/api``) and
    the load event doesn't wait on these XHR/fetch calls, so aborting them
    removes the pollution source without affecting ``window.pwaDb`` or page
    load. Must be armed before ``_open_and_wait`` navigates.
    """
    page.route("**/api/**", lambda route: route.abort())


def _delete_db(page: Page) -> None:
    """Delete the PWA DB so each test starts from a clean slate."""
    page.evaluate(
        """(name) => new Promise((resolve) => {
            try {
              const req = indexedDB.deleteDatabase(name);
              req.onsuccess = () => resolve();
              req.onerror = () => resolve();
              req.onblocked = () => resolve();
            } catch (_e) { resolve(); }
          })""",
        DB_NAME,
    )


def _open_via_db_js_and_read(page: Page) -> dict[str, Any]:
    """Open the DB through db.js and read back store 1 of ``queue:events``.

    Used by the schema-upgrade tests after they seed an older-version DB
    with a manually-opened connection that they then ``close()``. An
    IndexedDB ``close()`` only takes full effect once the connection's
    outstanding work drains, so under load db.js's v3 upgrade here can
    begin while that manual connection is still settling and be briefly
    blocked. db.js surfaces that as a distinct ``idb_blocked`` rejection
    *precisely so callers can retry* (a real consumer does the same when
    another tab is mid-close) — so retry until the block clears rather than
    letting the transient race fail the test. Every other open error still
    propagates immediately.

    Returns the opened DB's ``version``, sorted ``objectStoreNames``, and
    the seeded ``queue:events`` row.
    """
    return cast(
        "dict[str, Any]",
        page.evaluate(
            """async () => {
                let db = null;
                for (let attempt = 0; attempt < 40; attempt++) {
                  try {
                    db = await window.pwaDb.open();
                    break;
                  } catch (err) {
                    if (String(err && err.message) !== 'idb_blocked') throw err;
                    await new Promise((resolve) => setTimeout(resolve, 50));
                  }
                }
                if (!db) throw new Error('idb_blocked did not clear within retry budget');
                const row = await window.pwaDb.get('queue:events', 1);
                return {
                  version: db.version,
                  names: Array.from(db.objectStoreNames).sort(),
                  row,
                };
              }"""
        ),
    )


# ---------------------------------------------------------------------------
# 1. Fresh open creates every static store at version 3.
# ---------------------------------------------------------------------------


def test_fresh_open_creates_all_stores(live_server: LiveServer, page: Page) -> None:
    """After a fresh open() all six stores exist at version 3 (SNOW-482)."""
    _open_and_wait(page, live_server.url)
    _delete_db(page)

    stores = page.evaluate(
        """async () => {
            const db = await window.pwaDb.open();
            return {
              version: db.version,
              names: Array.from(db.objectStoreNames).sort(),
            };
          }"""
    )
    assert stores["version"] == 3
    assert stores["names"] == sorted(V3_STORES)


# ---------------------------------------------------------------------------
# 2. put / get / delete / getAll / count / clear round-trip on queue:events.
# ---------------------------------------------------------------------------


def test_round_trip_on_queue_events(live_server: LiveServer, page: Page) -> None:
    """put returns a key, get / getAll return the row, delete removes it."""
    _open_and_wait(page, live_server.url)
    _delete_db(page)

    result = page.evaluate(
        """async () => {
            const db = window.pwaDb;
            const store = 'queue:events';
            const key1 = await db.put(store, { event: 'pwa.sw.installed', ts: 1 });
            const key2 = await db.put(store, { event: 'pwa.sw.activated', ts: 2 });
            const one = await db.get(store, key1);
            const all = await db.getAll(store);
            const before = await db.count(store);
            await db.delete(store, key1);
            const after = await db.count(store);
            await db.clear(store);
            const cleared = await db.count(store);
            return { key1, key2, one, allLen: all.length, before, after, cleared };
          }"""
    )
    assert result["key1"] == 1
    assert result["key2"] == 2
    assert result["one"] == {"event": "pwa.sw.installed", "ts": 1, "id": 1}
    assert result["allLen"] == 2
    assert result["before"] == 2
    assert result["after"] == 1
    assert result["cleared"] == 0


# ---------------------------------------------------------------------------
# 3. context() shape.
# ---------------------------------------------------------------------------


def test_context_returns_eight_field_envelope(
    live_server: LiveServer, page: Page
) -> None:
    """context() returns the seven envelope-context keys with sane defaults.

    ``event``, ``timestamp`` and ``properties`` are the caller's
    responsibility per spec §16.1 — the helper covers the seven
    session/device context keys.
    """
    _open_and_wait(page, live_server.url)

    ctx = cast(dict[str, Any], page.evaluate("() => window.pwaDb.context()"))
    assert set(ctx.keys()) == {
        "session_id",
        "user_id",
        "client_version",
        "platform",
        "install_state",
        "sw_state",
        "connection",
    }
    # session_id is generated (random-ish string, non-empty).
    assert isinstance(ctx["session_id"], str) and ctx["session_id"]
    # user_id: anonymous by default in the test env.
    assert ctx["user_id"] in (None, "")
    # client_version comes from <meta name="pwa-app-version">.
    assert isinstance(ctx["client_version"], str)
    # platform: web / ios / android — headless Chromium reads as web.
    assert ctx["platform"] in {"web", "ios", "android"}
    # install_state: standalone / browser — Playwright is browser.
    assert ctx["install_state"] == "browser"
    # sw_state: activated / none. On first cold page load the SW hasn't
    # yet taken control, so 'none' is normal.
    assert ctx["sw_state"] in {"activated", "none"}
    # connection: unknown in headless (no NetworkInformation API).
    assert isinstance(ctx["connection"], str)


def test_context_is_stable_within_a_page(live_server: LiveServer, page: Page) -> None:
    """Two context() calls in the same page-load return the same session_id."""
    _open_and_wait(page, live_server.url)
    result = page.evaluate(
        """() => {
            const a = window.pwaDb.context();
            const b = window.pwaDb.context();
            return { same: a === b, aSid: a.session_id, bSid: b.session_id };
          }"""
    )
    assert result["same"] is True
    assert result["aSid"] == result["bSid"]


# ---------------------------------------------------------------------------
# 4. Reset Required on migration failure.
# ---------------------------------------------------------------------------


def test_reset_required_on_migration_failure(
    live_server: LiveServer, page: Page
) -> None:
    """A pre-existing higher-version DB blocks db.js's open (VersionError)
    and puts the app in the terminal Reset Required state.

    We can't mutate db.js's internal migration to throw from the test,
    but we can arrange a Reset-worthy failure by opening the DB at a
    version HIGHER than db.js knows about first — db.js's open() will
    then hit ``VersionError`` (browsers refuse to open at an OLDER
    version than the on-disk one), which routes through the same
    ``_enterResetRequired`` path.
    """
    # Fresh load; ensure DB is clean.
    _open_and_wait(page, live_server.url)
    _delete_db(page)

    # Open the DB at a version above db.js's DB_VERSION so the next
    # attempt from db.js can only fail.
    page.evaluate(
        """async (name) => {
            await new Promise((resolve, reject) => {
              const req = indexedDB.open(name, 99);
              req.onsuccess = () => { req.result.close(); resolve(); };
              req.onerror = () => reject(req.error);
              req.onupgradeneeded = () => {};
            });
          }""",
        DB_NAME,
    )

    # Now force db.js to attempt an open — it will hit VersionError.
    outcome = page.evaluate(
        """async () => {
            try {
              await window.pwaDb.open();
              return { opened: true };
            } catch (err) {
              return {
                opened: false,
                reset: window.pwaDb.isResetRequired(),
                overlayVisible: !document
                  .getElementById('pwa-reset-required')
                  .classList.contains('hidden'),
              };
            }
          }"""
    )

    assert outcome["opened"] is False, (
        f"pwaDb.open() should have rejected, got: {outcome}"
    )
    assert outcome["reset"] is True
    assert outcome["overlayVisible"] is True

    # Subsequent open() calls must also reject without hitting IndexedDB.
    still_rejected = page.evaluate(
        """async () => {
            try {
              await window.pwaDb.open();
              return false;
            } catch (_e) {
              return true;
            }
          }"""
    )
    assert still_rejected is True


# ---------------------------------------------------------------------------
# 5. Schema upgrade — a pre-existing v1 DB gains data:favourites (SNOW-418).
# ---------------------------------------------------------------------------


def test_upgrade_from_v1_adds_data_favourites(
    live_server: LiveServer, page: Page
) -> None:
    """A pre-existing version-1 DB (four stores) is migrated straight to
    the current version by db.js's own ``_runMigrations`` on the next
    ``open()`` — every missing store (``data:favourites`` from SNOW-418,
    ``log:sync`` from SNOW-482) is added in the single combined upgrade
    transaction, and the four original stores (with any of their
    existing data) are left untouched. ``_runMigrations`` has no
    per-version branches — it just creates whatever's missing from
    ``STORES`` — so a client jumping several versions in one ``open()``
    behaves the same as one jumping a single version.
    """
    _open_and_wait(page, live_server.url)
    _delete_db(page)

    # Simulate a pre-SNOW-418 client: open at version 1 with only the four
    # original stores, and write one row into queue:events so we can prove
    # the upgrade didn't wipe existing data.
    seeded = page.evaluate(
        """async (name) => {
            const db = await new Promise((resolve, reject) => {
              const req = indexedDB.open(name, 1);
              req.onupgradeneeded = (evt) => {
                const d = evt.target.result;
                d.createObjectStore('queue:mutations', { keyPath: 'id', autoIncrement: true });
                d.createObjectStore('queue:events', { keyPath: 'id', autoIncrement: true });
                d.createObjectStore('meta:sync', { keyPath: 'resource' });
                d.createObjectStore('meta:app', { keyPath: 'key' });
              };
              req.onsuccess = () => resolve(req.result);
              req.onerror = () => reject(req.error);
            });
            const putId = await new Promise((resolve, reject) => {
              const tx = db.transaction('queue:events', 'readwrite');
              const req = tx.objectStore('queue:events').put({ event: 'pre-existing' });
              req.onsuccess = () => resolve(req.result);
              req.onerror = () => reject(req.error);
            });
            db.close();
            return putId;
          }""",
        DB_NAME,
    )
    assert seeded == 1

    # Now let db.js open the same DB — its _runMigrations upgrade branch
    # must add every missing store, up to the current version, without
    # disturbing the seeded row.
    result = _open_via_db_js_and_read(page)
    assert result["version"] == 3
    assert result["names"] == sorted(V3_STORES)
    assert result["row"] == {"id": 1, "event": "pre-existing"}


# ---------------------------------------------------------------------------
# 5b. Schema upgrade — a pre-existing v2 DB gains log:sync (SNOW-482).
# ---------------------------------------------------------------------------


def test_upgrade_from_v2_adds_log_sync(live_server: LiveServer, page: Page) -> None:
    """A pre-existing version-2 DB (five stores, no ``log:sync``) is
    migrated to version 3 by db.js's own ``_runMigrations`` on the next
    ``open()`` — the new store is added and the five original stores
    (with any of their existing data) are left untouched.
    """
    _open_and_wait(page, live_server.url)
    _delete_db(page)

    # Simulate a pre-SNOW-482 client: open at version 2 with the five
    # V2_STORES, and write one row into queue:events so we can prove the
    # upgrade didn't wipe existing data.
    seeded = page.evaluate(
        """async (name) => {
            const db = await new Promise((resolve, reject) => {
              const req = indexedDB.open(name, 2);
              req.onupgradeneeded = (evt) => {
                const d = evt.target.result;
                d.createObjectStore('queue:mutations', { keyPath: 'id', autoIncrement: true });
                d.createObjectStore('queue:events', { keyPath: 'id', autoIncrement: true });
                d.createObjectStore('meta:sync', { keyPath: 'resource' });
                d.createObjectStore('meta:app', { keyPath: 'key' });
                d.createObjectStore('data:favourites', { keyPath: 'uuid' });
              };
              req.onsuccess = () => resolve(req.result);
              req.onerror = () => reject(req.error);
            });
            const putId = await new Promise((resolve, reject) => {
              const tx = db.transaction('queue:events', 'readwrite');
              const req = tx.objectStore('queue:events').put({ event: 'pre-existing' });
              req.onsuccess = () => resolve(req.result);
              req.onerror = () => reject(req.error);
            });
            db.close();
            return putId;
          }""",
        DB_NAME,
    )
    assert seeded == 1

    # Now let db.js open the same DB — its _runMigrations upgrade branch
    # must add log:sync without disturbing the seeded row.
    result = _open_via_db_js_and_read(page)
    assert result["version"] == 3
    assert result["names"] == sorted(V3_STORES)
    assert result["row"] == {"id": 1, "event": "pre-existing"}


# ---------------------------------------------------------------------------
# 6. appendSyncLog / getSyncLog (SNOW-482).
# ---------------------------------------------------------------------------


def test_append_sync_log_trims_to_newest_100(
    live_server: LiveServer, page: Page
) -> None:
    """appendSyncLog trims log:sync to the newest 100 rows.

    getAll(store, limit) returns the LOWEST keys first, which is the
    wrong end for a "keep the newest N" trim — this asserts the actual
    surviving rows are the newest 100 (paths "sync-1".."sync-100"), not
    the oldest.
    """
    _block_map_data_feeds(page)
    _open_and_wait(page, live_server.url)
    _delete_db(page)

    result = page.evaluate(
        """async () => {
            for (let i = 1; i <= 105; i++) {
              await window.pwaDb.appendSyncLog({ at: new Date().toISOString(), path: `sync-${i}` });
            }
            const count = await window.pwaDb.count('log:sync');
            const all = await window.pwaDb.getAll('log:sync');
            return { count, paths: all.map((row) => row.path).sort() };
          }"""
    )
    assert result["count"] == 100
    expected = sorted(f"sync-{i}" for i in range(6, 106))
    assert result["paths"] == expected


def test_get_sync_log_returns_newest_first(live_server: LiveServer, page: Page) -> None:
    """getSyncLog reads back rows newest-first, honouring ``limit``."""
    _block_map_data_feeds(page)
    _open_and_wait(page, live_server.url)
    _delete_db(page)

    result = page.evaluate(
        """async () => {
            for (let i = 1; i <= 5; i++) {
              await window.pwaDb.appendSyncLog({ at: new Date().toISOString(), path: `sync-${i}` });
            }
            const all = await window.pwaDb.getSyncLog();
            const limited = await window.pwaDb.getSyncLog(2);
            return {
              allPaths: all.map((row) => row.path),
              limitedPaths: limited.map((row) => row.path),
            };
          }"""
    )
    assert result["allPaths"] == ["sync-5", "sync-4", "sync-3", "sync-2", "sync-1"]
    assert result["limitedPaths"] == ["sync-5", "sync-4"]
