/*
 * tests/js/test_pwa_network_mode.js — Vitest unit tests for
 * static/js/pwa_network_mode.js (SNOW-922).
 *
 * The module exists because of one lock-out, and the tests are organised
 * around the four things that had to be true to fix it:
 *
 *   1. The mode can be READ with no ``db.js`` on the page. The recovery
 *      page loads no database layer, and a switch that cannot read the
 *      mode paints the wrong state at the moment the reader can least
 *      afford to be misled.
 *   2. The mode can be WRITTEN with no ``db.js``, and written to the same
 *      row ``sw.js`` hydrates from. A change the worker cannot see on its
 *      next boot is not a change.
 *   3. Persist happens BEFORE announce. That ordering is the whole
 *      point: announcing first leaves a window in which the worker holds
 *      the new mode and the disk the old one, and Chrome kills an idle
 *      worker after about thirty seconds — so a worker recycled inside
 *      that window comes back in the mode the user just left. For the
 *      ``'auto'`` direction that IS the original bug, re-opened.
 *   4. ``canOpenOffline`` answers false on every doubt. It gates a
 *      warning, and a warning withheld from someone whose app is not
 *      saved costs them the app.
 *
 * ``window.pwaDb`` is left undefined except in the block that is about
 * it: the recovery page is the context that matters most here, and it is
 * the one with no ``pwaDb``, so the raw path is the default rather than
 * the exception.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/pwa_network_mode.js';

const DB_NAME = 'snowdesk-pwa-v1';
const KEY = 'network.mode';

const api = () => window.pwaNetworkMode;

/** Delete the PWA database and wait for the deletion to complete. */
function deleteDb() {
  return new Promise((resolve) => {
    try {
      const req = indexedDB.deleteDatabase(DB_NAME);
      req.onsuccess = () => resolve();
      req.onerror = () => resolve();
      req.onblocked = () => resolve();
    } catch (_err) {
      resolve();
    }
  });
}

/**
 * Create the database with a ``meta:app`` store, optionally seeded with a
 * mode — standing in for the one ``db.js`` builds on a real device.
 *
 * @param {string|null} mode
 */
function seedDb(mode) {
  return new Promise((resolve) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore('meta:app', { keyPath: 'key' });
    };
    req.onsuccess = () => {
      const db = req.result;
      if (mode === null) {
        db.close();
        resolve();
        return;
      }
      const tx = db.transaction('meta:app', 'readwrite');
      tx.objectStore('meta:app').put({ key: KEY, value: mode });
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
    };
  });
}

/** Read the persisted mode back without going through the module. */
function readPersisted() {
  return new Promise((resolve) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('meta:app')) {
        db.close();
        resolve(null);
        return;
      }
      const get = db.transaction('meta:app', 'readonly').objectStore('meta:app').get(KEY);
      get.onsuccess = () => {
        db.close();
        resolve(get.result ? get.result.value : null);
      };
      get.onerror = () => {
        db.close();
        resolve(null);
      };
    };
    req.onerror = () => resolve(null);
  });
}

/**
 * A controlling worker that records what it is posted.
 *
 * ``reply`` decides what it answers a ``can-open-offline`` question with:
 * ``true``/``false`` to answer down the transferred port, or ``null`` to
 * answer nothing at all — the silent worker the budget exists for.
 *
 * @param {{reply?: boolean|null}} [options]
 */
function stubController(options) {
  const opts = options || {};
  const posted = [];
  Object.defineProperty(window.navigator, 'serviceWorker', {
    configurable: true,
    value: {
      controller: {
        postMessage: (data, transfer) => {
          posted.push(data);
          if (data && data.type === 'can-open-offline' && opts.reply !== null) {
            const port = transfer && transfer[0];
            port?.postMessage({ type: 'can-open-offline', canOpen: opts.reply });
          }
        },
      },
      addEventListener: () => {},
    },
  });
  return { posted };
}

/** Remove any controller, which is the state a page has before activation. */
function noController() {
  Object.defineProperty(window.navigator, 'serviceWorker', {
    configurable: true,
    value: { controller: null, addEventListener: () => {} },
  });
}

beforeEach(async () => {
  await deleteDb();
  delete window.pwaDb;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('coercion — the rule every inbound path shares', () => {
  it('passes the two offline modes through untouched', () => {
    expect(api().coerce('offline')).toBe('offline');
    expect(api().coerce('offline-forced')).toBe('offline-forced');
  });

  it('answers auto for anything it does not recognise', () => {
    // A row written by a different version of this code than the one
    // reading it is the case that matters: 'auto' is the only mode that
    // claims nothing, so an unreadable value must never strand anyone.
    ['aeroplane', '', null, undefined, 42, {}].forEach((value) => {
      expect(api().coerce(value)).toBe('auto');
    });
  });

  it('tells the user’s mode apart from the worker’s latch', () => {
    // The remedies are nothing alike: a latch lifts itself when a probe
    // finds a route, so there is nothing for the reader to press.
    expect(api().isForced('offline-forced')).toBe(true);
    expect(api().isForced('offline')).toBe(false);
    // But both stop the app calling the server, which is the question the
    // switch's own checked state asks.
    expect(api().blocksNetwork('offline')).toBe(true);
    expect(api().blocksNetwork('offline-forced')).toBe(true);
    expect(api().blocksNetwork('auto')).toBe(false);
  });
});

describe('reading the mode with no db.js — the recovery page’s case', () => {
  it('reads a forced mode back out of meta:app', async () => {
    await seedDb('offline-forced');

    expect(await api().read()).toBe('offline-forced');
  });

  it('answers auto when nothing has ever been written', async () => {
    await seedDb(null);

    expect(await api().read()).toBe('auto');
  });

  it('answers auto when the database does not exist at all', async () => {
    // A device that has never run db.js. The switch must still paint, and
    // it must paint the mode that claims nothing.
    expect(await api().read()).toBe('auto');
  });

  it('answers auto rather than hanging when the store is missing', async () => {
    // A worker-created database holds only `queue:mutations` — see
    // sw.js's `_openMutationsDb`. There is no mode on such a device.
    await new Promise((resolve) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore('queue:mutations', { keyPath: 'id' });
      };
      req.onsuccess = () => {
        req.result.close();
        resolve();
      };
    });

    expect(await api().read()).toBe('auto');
  });
});

describe('reading the mode through db.js — the app’s case', () => {
  it('prefers window.pwaDb where the app has loaded it', async () => {
    // The app's real database layer owns the schema version, the upgrade
    // path and the Reset Required state. An app-side read that went
    // round it would bypass all of that.
    const get = vi.fn().mockResolvedValue({ key: KEY, value: 'offline-forced' });
    window.pwaDb = { get, put: vi.fn() };

    expect(await api().read()).toBe('offline-forced');
    expect(get).toHaveBeenCalledWith('meta:app', KEY);
  });

  it('answers auto when that layer rejects', async () => {
    // Private mode, Reset Required, a blocked open. None of them is a
    // reason to tell the user they are offline.
    window.pwaDb = { get: vi.fn().mockRejectedValue(new Error('reset required')), put: vi.fn() };

    expect(await api().read()).toBe('auto');
  });
});

describe('setting the mode', () => {
  it('persists to the row sw.js hydrates from', async () => {
    await seedDb(null);
    stubController();

    await api().set('offline-forced');

    // The exact key sw.js's `_hydrateNetworkMode` looks for. A mode
    // written anywhere else is a mode the worker never learns.
    expect(await readPersisted()).toBe('offline-forced');
  });

  it('tells the worker, so the change takes effect before the next boot', async () => {
    await seedDb(null);
    const sw = stubController();

    await api().set('auto');

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('persists BEFORE it announces', async () => {
    // The ordering this module exists to own. Announcing first leaves a
    // window in which the worker is in the new mode and the disk still
    // holds the old one; an idle worker recycled inside that window comes
    // back in the mode the user just left, which for the 'auto' direction
    // is the lock-out this ticket fixed, re-opened.
    const order = [];
    window.pwaDb = {
      get: vi.fn(),
      put: vi.fn().mockImplementation(async () => {
        order.push('persist');
      }),
    };
    Object.defineProperty(window.navigator, 'serviceWorker', {
      configurable: true,
      value: {
        controller: {
          postMessage: () => order.push('announce'),
        },
        addEventListener: () => {},
      },
    });

    await api().set('auto');

    expect(order).toEqual(['persist', 'announce']);
  });

  it('still announces when the write fails', async () => {
    // A device that cannot be written to is one where the user has to say
    // so again after a restart. That is much better than the mode not
    // changing now, which is what a thrown write would cost.
    window.pwaDb = { get: vi.fn(), put: vi.fn().mockRejectedValue(new Error('quota')) };
    const sw = stubController();

    await expect(api().set('auto')).resolves.toBe('auto');
    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('coerces an unrecognised mode rather than persisting it', async () => {
    await seedDb(null);
    stubController();

    await api().set(/** @type {any} */ ('aeroplane'));

    expect(await readPersisted()).toBe('auto');
  });

  it('does not reject when there is no controller to announce to', async () => {
    // A page loaded before the worker activated has none. The persisted
    // row is re-asserted on the next boot, so the choice is not lost.
    await seedDb(null);
    noController();

    await expect(api().set('offline-forced')).resolves.toBe('offline-forced');
    expect(await readPersisted()).toBe('offline-forced');
  });
});

describe('canOpenOffline — the guard on the switch’s ON direction', () => {
  it('answers what the worker says', async () => {
    stubController({ reply: true });

    expect(await api().canOpenOffline()).toBe(true);
  });

  it('answers false when the worker says the app is not saved', async () => {
    stubController({ reply: false });

    expect(await api().canOpenOffline()).toBe(false);
  });

  it('asks the worker rather than reading the caches itself', async () => {
    // The worker is the thing that will or will not serve the navigation,
    // and it already holds the shell cache and the principal stamp. One
    // implementation of "would _networkFirstFallback find a page", not
    // two that can drift.
    const sw = stubController({ reply: true });

    await api().canOpenOffline();

    expect(sw.posted).toContainEqual({ type: 'can-open-offline' });
  });

  it('answers false with no controller at all', async () => {
    // Nothing is cached and nothing will be served, so the app will not
    // open — and there is no one to ask. Both readings point the same way.
    noController();

    expect(await api().canOpenOffline()).toBe(false);
  });

  it('answers false when the worker never replies', async () => {
    // A worker that does not answer must not hold a confirmation dialogue
    // open. False is also the safe direction: the user is warned.
    vi.useFakeTimers();
    stubController({ reply: null });

    const pending = api().canOpenOffline();
    await vi.advanceTimersByTimeAsync(3000);

    expect(await pending).toBe(false);
  });
});
