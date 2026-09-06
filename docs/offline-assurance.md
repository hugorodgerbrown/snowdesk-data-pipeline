---
name: offline-assurance
description: Weekly offline-maps suite — tests/offline/, tox -e offline, RecordingProxy, SNOWDESK_OFFLINE_SEED, #nav-offline-mode watertightness
status: current
last-reviewed: 2026-09-06
---

# Offline-maps assurance (weekly)

`tests/offline/` is a fourth test layer, on top of the three in
[`client-side-tests.md`](client-side-tests.md). It exists for one question
that none of the other three can answer:

> If a user downloads a region, switches the network off and opens the map
> on a mountain, do they see a map?

It runs weekly, off the merge path, with a fuzzed subject.

```bash
uv run tox -e offline                                  # this ISO week's draw
SNOWDESK_OFFLINE_SEED=2026-W37 uv run tox -e offline   # reproduce a red run
```

## Why it is not in `tests/e2e/`

Every rule that suite enforces, this one breaks. `bin/e2e-lint` caps the
Playwright suite at a couple of dozen short tests, each under 40 lines,
each a smoke check that runs on every PR. These tests are minutes long,
download several hundred real tiles from a live origin, and would either
blow that cap or have to shrink until they proved nothing. Different
question, different budget, different directory — and `bin/e2e-lint` is
scoped to `tests/e2e/`, so the two do not interfere.

## The three things that make it different

### 1. The network boundary is real

Every browser context runs behind `tests/offline/proxy.py`'s
`RecordingProxy`. This matters because the two tools a browser test would
normally reach for are both blind to service-worker traffic:
`page.context.set_offline` disables the network for the *page* and
`page.route` never sees a worker's own script fetches at all. Chromium
routes everything through a proxy, below all of that.

`proxy.playwright_proxy()` passes `bypass: "<-loopback>"`. Without it
Chromium bypasses the proxy for loopback, the live server is on loopback,
and every same-origin request would be invisible and unblockable — which
would make every assertion in the suite vacuous while it went on passing.

Three modes, and they are not interchangeable:

| Mode | What the proxy does | What the app sees |
|---|---|---|
| `pass` | Forwards | A healthy network |
| `reject` | RST (`SO_LINGER` 0) | `fetch` **rejects** — the radio is off |
| `blackhole` | Accepts, answers nothing | `fetch` **hangs** — the captive portal / Underground case |

`reject` deliberately does not answer `502`: a 502 is a successful HTTP
exchange, `fetch` resolves with it, and not one `catch` branch in the app
runs. `blackhole` is the condition
[`bounded-offline-read-paths.md`](decisions/bounded-offline-read-paths.md)
was written for and the only one that exercises the latch.

TLS is tunnelled, not intercepted. Reading tile URLs out of HTTPS would
need a minted CA trusted in the browser profile, and the assertions do not
need it: "zero connections were opened to the tile origin" is as strong as
"zero requests", and the request-level detail the suite does need is
same-origin plain HTTP.

### 2. Going offline is done through the product

The suite presses `#nav-offline-mode` — the switch in the account menu, the
one a user presses — and waits for the header connectivity symbol, which
`pwa_offline.js` repaints only after the worker acknowledges the mode.

`test_offline_toggle_is_watertight.py` is the suite's foundation: with the
network *fully available*, it switches Offline mode on and asserts the
proxy sees nothing. Every other test establishes "no network" with that
same switch, so if it leaks they are all measuring a map that quietly had
the internet.

Two exemptions, and they are the only ones: `/sw.js` (the worker update
check — a worker cannot intercept itself) and `/csp/report-uri/` (issued by
the browser's policy engine, specified to bypass service workers). The
modules `sw.js` pulls in with `importScripts` are exempt too, and are read
out of `sw.js`'s source rather than listed, so the list cannot drift
silently in the direction that weakens the test.

Render tests call `go_offline()`, which presses the switch **and** cuts the
proxy. Belt and braces on purpose: a render test whose only guarantee is
the switch is measuring the switch.

### 3. The subject is fuzzed

`tests/offline/fuzz.py` draws the region, the basemap, the probe zooms and
the bearing "outside coverage" lies on, from a seed. The seed defaults to
the ISO week (`2026-W37`), so a scheduled run picks new ground each week
while every re-run within that week is identical — a random default would
make a red run unreproducible, which in a weekly suite means unfixable.

Two constraints on the draw, both real:

- Regions over `MAX_FUZZ_TILE_COUNT` (220) are excluded. A courtesy to the
  tile origin, not a correctness bound.
- `BASEMAP_COUNTRIES` filters out basemaps that do not draw the drawn
  region's country. IGN Plan covers France and basemap.at covers Austria,
  and outside their country they render blank *by design*, so drawing
  basemap.at for a Swiss region would download a few hundred legitimately
  empty tiles and then report the product broken.

## How "did it render" is measured

`OfflineMapPage.basemap_ink()` returns the fraction of the map canvas that
differs from the style's background colour.

Pixels rather than cache entries, because
[`a-downloaded-area-is-verified-by-what-it-renders.md`](decisions/a-downloaded-area-is-verified-by-what-it-renders.md)
is about exactly this: SNOW-843 shipped three defects where every surface
agreed the area was downloaded and the map was blank. Pixels rather than a
golden image, because the subject is fuzzed and a golden would need
regenerating per region.

Two things make the number mean what it says:

- **Snowdesk's own overlays are hidden** for the measurement. The
  choropleth, region outlines and downloaded-tile squares are on the same
  canvas, cover most of Switzerland, and keep painting when the basemap
  does not. With them visible an uncovered viewport measured ~10%
  "coverage". The split is structural — every Snowdesk overlay is fed by a
  `geojson` source, every basemap layer by the style's `vector`/`raster`
  sources — so a new overlay is excluded automatically.
- **The passive basemap cache is discarded** before the coverage-edge
  tests. `snowdesk-basemap-v1` holds whatever the user has looked at, and
  merely opening the map caches a country-level view that MapLibre will
  overzoom anywhere. With it warm, ground 100 km from any download still
  drew 34%. Only `snowdesk-basemap-pinned-*` is a promise the product
  makes, so only that is left in place.

Thresholds are set from measurement, not taste — the numbers and the
measured separation are in the test module's own comment. Drawn states
sit at 28–57%, blank states at a constant 3.3%; the thresholds sit in the
middle of that order-of-magnitude gap.

## When the weekly run goes red

It fails loudly and gates nothing (see the header comment in
`.github/workflows/offline-assurance.yml` for why). The job summary carries
the seed; failures carry the console, the unhandled rejections and
MapLibre's own errors, because "the map was blank" on its own is not
actionable.

Reproduce with the seed, then read the assertion — each one names what it
expected, what it got, and what would have to be true for the result to be
correct.
