# PWA shell

Snowdesk is a Progressive Web App. Every public page registers a small
service worker that caches the application shell on first visit, so the
**second** load of any page paints instantly without the user having to
opt in.

This document is the agent-facing reference for that shell — what the
SW caches, how to bump the cache version, and how to regenerate the
manifest icons.

## What ships

| Path | Role |
|------|------|
| `static/manifest.webmanifest` | Web app manifest. Declares name, icons, theme/background colour, `start_url=/`, `scope=/`, `display=standalone`. Linked from `public/templates/public/base.html`. |
| `static/js/sw.js` | The service worker itself (~150 lines). Stale-while-revalidate for static shell + the regions GeoJSON; network-first for HTML navigations; network-only for everything else. |
| `static/js/sw_register.js` | Registers `/sw.js` at root scope on every public page. Loaded `defer` from `base.html`. Also drives the `#sw-update-banner` (see "Update strategy" below). |
| `public/views.py::serve_sw` | Serves `/sw.js` with the `Service-Worker-Allowed: /` and `Cache-Control: no-cache` headers required for root-scope control + prompt SW updates. URL is registered in `public/urls.py`. |
| `static/icons/pwa/` | Manifest icons: 192, 512, and a 512 maskable variant. Generated from `static/favicon.svg` by `bin/build-pwa-icons`. |

## Update strategy

The SW calls `self.skipWaiting()` on `install` so the new version takes
over on the next navigation, but it deliberately does **not** call
`self.clients.claim()`. Pairing `claim()` with a `controllerchange`-based
auto-reload in `sw_register.js` produced a tight reload loop in dev — every
navigation re-triggered the SW update check. Without `claim()`, the new SW
activates immediately but only controls an open tab on its next natural
navigation.

To surface the pending update to the user, `sw_register.js` reveals a
fixed bottom banner (`#sw-update-banner` in `base.html`) whenever a freshly
installed SW is waiting and the page is still controlled by the old one. The
banner offers two actions:

- **Reload** — navigates the page, picking up the new shell.
- **×** — dismisses the banner for the rest of the tab's lifetime.

Trade-off: in-flight tabs no longer auto-reload on SW activation. The banner
makes the update visible without the loop.

The banner markup is baked into `public/templates/public/base.html`; every
user-visible string is wrapped in `{% trans %}`. The JS in `sw_register.js`
only toggles the `hidden`/`flex` class pair (the HTML5 `hidden` attribute
would lose to Tailwind's `flex` utility in the cascade).

### How the trigger fires

The browser detects an SW update by **byte-comparing** the freshly fetched
`/sw.js` against the registered version. Anything that changes the bytes
(bumping `CACHE_VERSION`, adding a comment, editing a strategy) qualifies as
an update; identical bytes do not. The check runs on every navigation under
the SW's scope, accelerated by the `Cache-Control: no-cache` header that
`serve_sw` returns.

When the bytes differ, the SW lifecycle plays out as:

```
fetch /sw.js  →  install (skipWaiting)  →  installed  →  activating  →  activated
                                              │
                                              └── statechange listener in
                                                  sw_register.js fires here.
                                                  If navigator.serviceWorker.controller
                                                  is non-null (= an old SW is still
                                                  controlling the tab), the banner
                                                  is revealed.
```

The `controller` check suppresses the banner on first-time installs (when
no SW was previously controlling) — the user is not "updating" anything,
they are seeing the SW for the first time.

### Testing the banner locally

There is a chicken-and-egg quirk on the first deploy that introduces this
banner: any browser that already has the **old** `sw_register.js` cached
will keep running it (the SW serves `sw_register.js` from its own cache via
stale-while-revalidate) and will never reveal the banner — the old script
literally has no banner-reveal code. The new `sw_register.js` lands in the
SW cache during that first stale-while-revalidate fetch, but it does not
**run** until the next page load.

Concretely, on a tab that was registered against the pre-banner SW:

1. **Reload 1** — old `sw_register.js` runs (no banner logic). New
   `sw_register.js` arrives in the cache via stale-while-revalidate.
2. **Reload 2** — new `sw_register.js` runs; banner logic is now armed.
   But there is no pending update, so nothing is shown.
3. Bump `CACHE_VERSION` and **Reload 3** — banner appears.

To skip steps 1 and 2 in dev (or to test the banner deliberately), unregister
the SW once and reload:

```js
// DevTools console, on any page of the site:
const regs = await navigator.serviceWorker.getRegistrations();
for (const r of regs) await r.unregister();
const names = await caches.keys();
for (const n of names) await caches.delete(n);
location.reload();
```

After the reload, the new banner-aware `sw_register.js` is running. From
this point any byte change to `static/js/sw.js` (most simply a bump of
`CACHE_VERSION` from `'snowdesk-shell-v1'` to `'snowdesk-shell-v2'`) will
surface the banner on the next navigation.

In production this bootstrap happens transparently — every browser
eventually picks up the new register script via stale-while-revalidate, and
from then on every shell update fires the banner. The first roll-out of the
banner itself is a one-deploy burn-in.

---

There is **no precache manifest endpoint** and **no "Save offline"
button**. The previous SNOW-9 design (opt-in chunked precache for the
map shell + tiles) was retired in SNOW-79 — it didn't deliver any
benefit until the user clicked, was the source of "stuck on stale
data" reports, and the install affordance never materialised because
the manifest had no icons.

## Installability checklist

The browser shows its native install affordance only when **all** of
the following are true:

1. Page served over HTTPS (Render handles this on `*.onrender.com`).
2. `<link rel="manifest" href="…">` present — added in `base.html`.
3. Manifest declares `name`, `short_name`, `start_url`, `scope`,
   `display: standalone`, `theme_color`, `background_color` — already
   set in `static/manifest.webmanifest`. Both `start_url` and `scope`
   are `/` so the installed app opens on the home page and every
   public path (map, bulletins, subscribe, terms) stays inside the
   standalone window. Without `scope: /`, the W3C default would be the
   directory of `start_url`, which would push every other page out
   into a regular browser tab (SNOW-87).
4. Manifest `icons[]` carries at least one 192×192 and one 512×512
   PNG — generated from `static/favicon.svg` by
   `bin/build-pwa-icons`.
5. A registered service worker with a `fetch` handler — added by
   `sw_register.js`, served by `serve_sw`.

If the install affordance disappears, walk back through the list. The
icons are the brittle item — see "Regenerating icons" below.

## Cache strategy

The SW classifies every fetch into one of three buckets:

- **`static`** — same-origin requests for assets in
  `STATIC_SHELL_EXTENSIONS` (CSS, JS, SVG, PNG/JPG/WEBP, ICO,
  WOFF/WOFF2, WEBMANIFEST) plus the paths in `STATIC_PATHS` (currently
  just `/api/regions.geojson`). Strategy: **stale-while-revalidate**.
  The cache is served immediately if hit; a background fetch refreshes
  the entry for next time.

- **`navigate`** — HTML navigations (`request.mode === 'navigate'` or
  destination `document`). Strategy: **network-first** with cache
  fallback so an offline reload still surfaces the last-seen page.

- **`network`** — everything else: bulletin JSON
  (`/api/region/<id>/summary/`), today-summaries, calendar partials,
  resort feeds that change with the bulletin, and all third-party
  origins (MapLibre CDN, OpenFreeMap tiles). Strategy: **network only**
  — no `event.respondWith()` call, the SW is bypassed entirely. This
  is deliberate: a stale avalanche rating could mislead a user.

The exact rules live in `_classify()` and the two strategy helpers
`_staleWhileRevalidate()` / `_networkFirst()` in `sw.js`. To add a new
URL pattern to the cache, edit those — don't introduce additional
fetch strategies without first checking whether the data class is one
users must always see fresh.

## Cache version bump

`sw.js` declares a single `CACHE_VERSION` constant at the top of the
file (`snowdesk-shell-v1` at the time of writing). On `activate`, the
SW deletes any cache whose name is not the current version. Bump it
when:

- The cache contract changes (new asset class added, classification
  rules altered in a way that would re-serve stale entries
  incorrectly).
- A bug in the previous SW could have poisoned caches at scale.

Day-to-day asset edits (new CSS rule, new JS function, new icon) do
**not** require a version bump — stale-while-revalidate handles that
automatically on the next page view.

## Regenerating icons

The three PNGs in `static/icons/pwa/` are deterministic outputs of
`bin/build-pwa-icons`, which uses [`sharp`](https://sharp.pixelplumbing.com/)
to render `static/favicon.svg` at the manifest sizes. To rebuild:

```bash
npm install      # pulls sharp if not already installed
npm run build:icons
```

Output:

- `icon-192.png` — 192×192, `purpose: any`.
- `icon-512.png` — 512×512, `purpose: any`. Used by Chrome's install
  prompt and the Android home-screen launcher.
- `icon-maskable-512.png` — 512×512, `purpose: maskable`. The artwork
  is padded inside an 80% safe zone over the manifest's
  `background_color` so Android's adaptive-icon mask doesn't crop the
  glyph.

The PNGs are checked in. There's no Render-side regen step — when
`favicon.svg` changes, run `npm run build:icons` and commit the new
PNGs alongside the SVG edit.

## Tests

- `tests/public/test_offline_api.py::test_serve_sw_*` — confirms
  `/sw.js` returns 200 with `Service-Worker-Allowed: /` and
  `Cache-Control: no-cache`, and that the script body registers at
  least one event listener.
- `tests/public/test_pwa_manifest.py` — asserts the manifest declares
  non-empty `icons[]`, includes both 192 and 512 sizes, and carries a
  `purpose: maskable` entry.
- `tests/public/test_map_page.py` — the inverted offline-toggle
  assertion: `#offline-toggle` must **not** appear in the rendered
  map page.

There are no unit tests for the SW's runtime behaviour — service
workers run inside a browser context that pytest can't replicate
faithfully. Manual verification:

1. `npm run lh` — Lighthouse PWA audit on `/` and a bulletin page.
2. Open the page in Chrome, install via the address-bar affordance,
   confirm the launcher icon and splash. Reload and confirm the second
   load uses cached shell entries (DevTools → Network → "Disable cache"
   off → reload should show `(ServiceWorker)` against CSS/JS rows).
