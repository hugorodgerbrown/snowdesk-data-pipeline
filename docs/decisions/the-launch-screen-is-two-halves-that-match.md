---
name: the-launch-screen-is-two-halves-that-match
description: PWA splash — apple-touch-startup-image, bin/build-pwa-splash, splash-manifest.json, the in-app launch shell; iOS never used background_color
status: current
last-reviewed: 2026-09-09
---

# The launch screen is two halves that have to match

**Decision.** An installed Snowdesk launch is covered by two separate
mechanisms drawn to be indistinguishable: the OS launch screen
(`apple-touch-startup-image` PNGs from `bin/build-pwa-splash`) from tap
to first paint, and the in-app launch shell
(`templates/includes/_pwa_launch_shell.html`) from first paint until the
map is ready. Both paint `--color-bg` with the home-screen tile centred
at the same proportion of the viewport's shorter edge. The in-app half
shows only for a cold launch of the installed app.

**Why.** Neither half is sufficient alone.

Without the first, iOS paints **white** for the whole cold boot. It does
not fall back to the manifest's `background_color` — the assumption
`docs/offline-map.md` recorded for the project's whole life, which is
why every iPhone install launched to a blank screen.

Without the second, the app still appears to hang: iOS drops its launch
screen at first paint, and on the map page first paint is chrome over an
empty `#map`, because MapLibre fetches its style and tiles with `fetch()`
and nothing holds the page back for them.

They have to *match* because the seam between them is invisible only if
there is nothing to see. Same background, same tile, same size, same
position — the handover then reads as one continuous splash that happens
to change owner halfway through. This is why the mark is absolutely
centred with the label taken out of flow: centring the pair as a group
would drop the tile ~20px below where iOS just drew it, producing a jump
at the exact moment the feature is trying to be invisible.

**Consequences.**

- **The composition is coupled in two places.** `ICON_RATIO` /
  `ICON_MIN_CSS` / `ICON_MAX_CSS` in `bin/build-pwa-splash` and
  `--pwa-launch-mark` in `src/css/main.css` express the same clamp.
  Moving one without the other reintroduces the jump.
- **`--color-bg` is load-bearing in three places** — the PNGs, the shell,
  and the manifest's `background_color`. `tests/public/test_pwa_splash.py`
  reads the token out of the stylesheet so drift fails a test rather than
  shipping a flash.
- **The device matrix lives only in the build script.** It writes
  `splash-manifest.json`; the Python side reads it. A second list in
  Python would drift, and drift here is silent — iOS 404s the image and
  falls back to white on one model, with nothing logged.
- **68 checked-in PNGs and 68 `<link>` tags on every page.** The tags are
  near-identical strings that compress to a fraction of their ~14 KB.
  Emitting them only to iOS user agents would add `Vary: User-Agent` to
  every public page and fragment the shared cache — a much worse trade.
- **The in-app half must be able to fail safely.** It covers the entire
  app, so a dismissal that never runs is an outage, not a cosmetic bug.
  Hence three JS signals plus a CSS failsafe keyframe that hides it at
  ten seconds whether or not any script parsed.
- **Android keeps a light-only launch screen.** The manifest has no
  per-scheme `background_color`, so only iOS (via `prefers-color-scheme`
  on the links) follows the theme. A user who pins a theme in
  `localStorage` against their OS setting gets the OS scheme, because
  nothing can read that preference before the page runs.
- **Staging has no splash set of its own.** The staging tell stays the
  amber tile and status bar, where a tester chooses the app.
