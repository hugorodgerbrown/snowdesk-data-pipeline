---
name: overlay-primitives
description: Four DS overlay primitives (banner/toast/sheet/modal), the data-action=dismiss + data-overlay contract, and the ordered z-index token scheme
status: current
last-reviewed: 2026-07-21
---

# Overlay primitives, the shared dismiss contract, and the z-index scheme

**Decision.** SNOW-486 consolidated the site's overlay/notification/popup
surfaces onto four DS primitives, one shared dismiss mechanism, and one
ordered z-index token scheme, replacing thirteen hand-rolled
implementations that each carried their own markup, hide idiom, dismiss
control, and z-index literal.

## The four primitives

1. **Banner** — `templates/includes/_overlay_banner.html`. Persistent,
   condition-driven. Two positional variants: **strip** (full-width in-flow
   edge bar, e.g. the off-season notice) and **floating** (fixed card with an
   optional icon/CTA/dismiss, anchored bottom-centre by default or top-centre
   via `position="top"`). Migrated the sw-update banner, the Android/Chromium
   + iOS install prompts, and the **off-map nudge** (`#offmap-banner`,
   `position="top"`, `icon="location-off"`) — the map.js reveal keeps its 7s
   auto-dismiss timer but the card shape and the "×" are now the shared
   primitive + `overlays.js`. Because it floats on every viewport it no
   longer pushes the map down on mobile (a deliberate convergence onto the
   one nudge shape).
2. **Toast** — `templates/includes/_toast.html`. Transient, bottom-centred
   pill. Extended with an opt-in `timeout` attribute (auto-dismiss via the
   shared controller in `static/js/overlays.js`).
3. **Sheet** — `templates/includes/_overlay_sheet.html`. Fly-out panel
   shared by the favourite and report map surfaces; content is injected by
   the owning JS module at open time.
4. **Modal** — `templates/includes/_overlay_modal.html`. Full-screen
   backdrop + centred card, CTA-only by design (no dismiss control). Shared
   shell for the two PWA modals (forced update, reset required).

`#home-intro` and `#map-help-overlay` (the intro card and the coachmark
tour) are structurally bespoke and are **not** reshaped into a primitive
partial — they only adopt the shared dismiss convention and the map-scoped
z-index band described below.

## Documented exceptions

Three surfaces stay on their own templates rather than being forced through
a primitive, because doing so would change behaviour, not just markup:

- **`_offline_banner.html`** — a `<details>` disclosure with spec-mandated
  (`docs/offline-first.md`) freshness internals that must paint from
  IndexedDB on a cold offline launch. In-flow (not `fixed`), so it carries
  no z-index token at all — it pushes page content down rather than
  overlapping it.
- **`_pwa_install_prompt.html`'s iOS variant** — a 3-icon instructional
  diagram with no CTA, structurally unlike the banner's icon+title+body+CTA
  shape. Adopts the shared `--z-banner` token and the `data-overlay` /
  `data-action="dismiss"` convention even though it isn't rendered through
  `_overlay_banner.html`.
- **`_toast_banner.html`** (the mutation-queue permanent-failure banner) —
  owns a custom slide-down transition (`static/js/mutation_queue.js` adds
  `hidden` only once the transition finishes, not on the click itself).
  The shared dismiss handler would hide it immediately on click and
  collapse the animation, so it deliberately does **not** carry
  `data-overlay`; `mutation_queue.js` keeps its own direct listener on the
  "×". It shares the `--z-toast` token with `_toast.html` by numeric value
  even though its dismiss isn't handler-driven.

## The shared dismiss contract

One delegated `document` click listener, `static/js/overlays.js` (loaded on
every public page, deferred). On a click of `[data-action="dismiss"]`:
finds the closest `[data-overlay]` ancestor, hides it using the idiom the
element declares (`data-overlay-hide="class"` → toggle the Tailwind
`hidden` *class*; default → toggle the HTML5 `hidden` *attribute*),
optionally persists a `data-overlay-persist="key=value"` declaration to
localStorage, then dispatches a bubbling `overlay:dismissed` CustomEvent
(`detail.overlay` = the element).

Two hide idioms coexist by design and are never flipped for an existing
element — shell banners/toasts/modals use the Tailwind `hidden` class (JS
elsewhere already toggles `classList` on them); map-internal overlays and
sheets (`#home-intro`, `#map-help-overlay`, `#favourite-sheet`,
`#report-sheet`) use the HTML5 `hidden` attribute. Playwright specs hard-code
each idiom, so a rename here is a breaking change for the matching e2e spec.

Overlays needing teardown beyond hide (favourites/report → deactivate the
place-picker + clear injected content; home-intro/map-help → persist +
restore focus) listen for `overlay:dismissed` rather than binding their own
×-click handler.

## The z-index token scheme

`src/css/main.css`'s `@theme` block defines one ordered global scale:

```
--z-banner: 40;   /* persistent banners: sw-update, install prompts, off-season bar */
--z-toast:  50;   /* transient toasts: share, mutation-queue, htmx-error            */
--z-popup:  60;   /* sheets: favourite / report fly-outs                            */
--z-modal:  70;   /* full-screen modals: forced update, reset required              */
```

Final stacking order is modal > popup > toast > banner — a deliberate
reorder from the pre-SNOW-486 state, which had toasts stacking above
sheets.

`#map`'s own stacking context (`static/css/map.css`) is a **separate,
map-scoped z band** (`#home-intro` / `#map-help-overlay` sit within it).
These elements are positioned against map elements and are intentionally
not lifted into the global scale.

## Consequences

- Any new persistent/transient/fly-out/full-screen overlay should reach for
  one of the four primitives first; a genuinely new shape needs the same
  justification as the three documented exceptions above.
- A new `data-action="dismiss"` control only needs a `[data-overlay]`
  ancestor with the right `data-overlay-hide` declaration — no per-page JS
  required, unless the overlay owns a transition the generic hide would
  short-circuit (see `_toast_banner.html` above).
- Any change to `static/js/overlays.js` or a primitive partial that touches
  shell JS/CSS requires a `CACHE_VERSION` bump in `static/js/sw.js`, or
  returning users get the stale shell.
