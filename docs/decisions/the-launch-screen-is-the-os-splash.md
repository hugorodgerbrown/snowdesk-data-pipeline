---
name: the-launch-screen-is-the-os-splash
description: PWA splash — apple-touch-startup-image, bin/build-pwa-splash, maskable icon; no in-app overlay after first paint
status: current
last-reviewed: 2026-10-18
---

# The launch screen is the OS's splash, and nothing continues it

**Decision.** An installed Snowdesk launch is covered by the OS launch
screen alone, from tap to first paint, and then the page paints. On iOS
that screen is the `apple-touch-startup-image` PNGs from
`bin/build-pwa-splash`. On Android, Chrome generates it from the manifest:
`background_color` and the maskable icon cropped to a circle. The page draws
no overlay of its own to carry the splash on while the map loads.

**Why.** The OS screen is required. Without startup images iOS paints
**white** for the whole cold boot. It does not fall back to the manifest's
`background_color`, which `docs/offline-map.md` assumed for the project's
whole life, so every iPhone install launched to a blank screen.

A page overlay is not. SNOW-878 added one: an in-app copy of the splash,
shown from first paint until the map was ready, so the user never saw the
chrome over an empty map. SNOW-1112 tried three times to make its
hand-over invisible:

1. per-environment artwork;
2. Android's documented splash geometry;
3. the geometry measured off the device.

The third version was the same size and in the same place, and a
recording on a Pixel 9 (Android 17) still showed a flash at the
hand-over. A seam we can't close costs more than the frame of empty map it
hid, so the overlay, its script, its styles and its tests were removed.

**Consequences.**

- **The first paint is the map page as it is.** That is the nav and
  controls over a map still fetching its style and tiles. Anything that
  makes that frame better has to happen in the page, not in front of it.
- **`--color-bg` is load-bearing twice**: in the PNGs and in the manifest's
  `background_color`, so the splash hands over to a page painting the same
  colour. `tests/public/test_pwa_splash.py` and
  `tests/public/test_pwa_launch_parity.py` read the token out of the
  stylesheet, so drift fails a test rather than shipping a flash.
- **The status bar is not a staging tell.** Android paints the splash's
  status bar in `background_color` and switches to `theme_color` at first
  paint, so both are equal on every environment. The staging tell is the
  amber tile alone.
- **The device matrix lives only in the build script.** It writes
  `splash-manifest.json`; the Python side reads it. A second list in
  Python would drift, and drift here is silent: iOS 404s the image and
  falls back to white on one model, with nothing logged.
- **68 checked-in PNGs and 68 `<link>` tags on every page.** The tags are
  near-identical strings that compress to a fraction of their ~14 KB.
  Emitting them only to iOS user agents would add `Vary: User-Agent` to
  every public page and fragment the shared cache, which is a much worse
  trade.
- **Android keeps a light-only launch screen.** The manifest has no
  per-scheme `background_color`, so only iOS (via `prefers-color-scheme`
  on the links) follows the theme.
- **Staging has no iOS splash set of its own.** 68 more PNGs to tint a
  half-second launch screen is a poor trade.

The current contract, and how to verify it on a device, is in
[`docs/pwa-launch.md`](../pwa-launch.md).
