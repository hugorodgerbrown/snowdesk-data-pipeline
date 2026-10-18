---
name: the-launch-screen-is-two-halves-that-match
description: PWA splash — apple-touch-startup-image, bin/build-pwa-splash, the launch shell; Android circle mark; theme_color equals background_color
status: current
last-reviewed: 2026-10-18
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

**Amended by SNOW-1112 (2026-10-18).** The same principle, now applied to
Android, which this record had treated as solved by the manifest:

- **The shell draws what the platform's splash drew.** Android's
  generated splash shows the manifest's maskable icon cropped to a circle,
  not the tile. So the shell has two marks, and its inline gate picks one:
  the circle-clipped maskable icon by default, and the tile on iOS. The
  composition is no longer "the same everywhere" but "the same as the
  screen it replaces".
- **Every value comes from `PWAEnvironmentIdentity`.** The shell's `src`
  was a hardcoded production path, so a staging install launched from the
  amber icon onto the green one. The icons, the background and
  `theme_color` are now read from the identity by the manifest and the
  shell alike, and `tests/public/test_pwa_launch_parity.py` compares the
  two rendered outputs.
- **The status bar is no longer a staging tell.** Android paints the
  splash's status bar in `background_color` and switches to `theme_color`
  at first paint, so the amber (staging) and near-black (production)
  `theme_color` were a colour flip at the handoff. Both now equal
  `background_color`. The previous consequence "the staging tell stays the
  amber tile and status bar" becomes the tile alone.

The current contract, and how to verify it on a device, is in
[`docs/pwa-launch.md`](../pwa-launch.md).

**Amended again by SNOW-1112 (2026-10-18, after a device recording).**
Matching still governs the handover. It no longer governs the whole wait:

- **The handover matches the device, not the documentation.** On a Pixel 9
  running Android 17, the splash shows the whole maskable icon as a 240dp
  circle, not the documented 160dp circle cropped to the middle two thirds.
  The shell drew the documented figures, so the logo shrank at the
  handover. It now draws what the device drew.
- **After the handover, the shell becomes a loading state on purpose.** A
  matched splash held over the whole wait hid the app without saying
  anything. So, a moment after the handover, the background fades to show
  the nav and the empty map, and the mark eases down to a small pulsing
  circle. A deliberate change that moves reads as loading, where a change
  in one frame reads as a glitch.
- **No visible "Loading…".** Text appearing under the logo is a change at
  the handover, and the label and its out-of-flow positioning (above) are
  gone. The `--pwa-launch-mark` variable went with them; the iOS clamp
  now lives on the tile rule itself, still coupled to `bin/build-pwa-splash`.
