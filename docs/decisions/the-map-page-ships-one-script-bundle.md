---
name: the-map-page-ships-one-script-bundle
description: Map page JS bundles — static/js/bundles/*.txt, {% js_bundle %}, bin/bundle-js, JS_BUNDLES; concatenation, no source map
status: current
last-reviewed: 2026-11-03
---

# The map page ships its scripts as two bundles

**Decision** (SNOW-1412). On staging and production the map page loads its
~125 first-party classic scripts as two files,
`static/bundles/map_surfaces.bundle.js` and `static/bundles/map.bundle.js`,
instead of one `<script defer>` each. Development and tests still load every
file on its own.

## Why

Each script was its own entry in the service worker's shell cache. A device
could hold some and not others: after the deploy-time carry dropped the tail
(see [the shell re-warm](the-shell-is-rewarmed-after-an-activation.md)),
after a warm failed part-way, after an eviction. The map then booted without
some of its features, and the offline audit reported "The app opens: No" on
a map that still opened. Raising `SHELL_SUBRESOURCE_LIMIT` to 400 fixed the
first cause. Bundling removes the class: a file is cached or it is not.

## How

- **The lists are the order.** `static/js/bundles/<name>.txt` names one file
  per line, relative to `static/js/`, in execution order; `#` lines carry
  the ordering notes that used to sit beside each script tag. A text file
  rather than Python or JSON so `bin/bundle-js` (Node) and the template tag
  (Python) read the same thing with a few lines each.
- **`{% js_bundle "map" %}`** (`apps/public/templatetags/js_bundles.py`)
  renders the built bundle when `settings.JS_BUNDLES` is on, and every
  member in list order when it is off. Production and perf set it; staging
  inherits it from production. A missing list fails the render.
- **`bin/bundle-js`** joins each list after `bin/minify-js`, in
  `bin/build.sh`, `npm run build:js` and the Lighthouse workflow. The output
  is gitignored, as minify-js's is.

## Two bundles, not one

`public/base.html` renders its own end-of-body scripts (`db.js`,
`pwa_install.js` and the rest) after `{% block content %}` and before
`{% block extra_js %}`. The surface partials' scripts ran before them and
the map's after, so `map_surfaces` closes the content block and `map` opens
`extra_js`. One bundle would have moved one side across the other.

## Plain concatenation, not a module bundler

The members share one global lexical scope: a top-level `const` in
`map_state.js` is a bare identifier in `map.js`
([map-and-api.md](../map-and-api.md#module-layout-snow-610)). A module
bundler wraps each file in a function and that sharing stops. Joining the
files keeps it. The `\n;\n` separator stops one file's last expression
running into the next. A member opening with a `'use strict'` directive
would change the strictness of the whole bundle, so `bin/bundle-js` refuses
one.

## Accepted trade-offs

- **A top-level throw stops every later member**, where a separate file
  stopped only itself. Wrapping members in `try {}` would block-scope their
  top-level bindings and break the shared scope. `error_reporting.js` loads
  first, from `base.html`, so the throw is still reported.
- **No source map** (Hugo, 2026-11-03). Nothing reads production maps
  today. Each member's `//# sourceMappingURL=` line is stripped, since it
  would point at the wrong offsets. A step that uploads maps would change
  this.
- **Scripts that guard themselves load for everyone.** The route-draw six,
  `today_panel.js`, `map_mode.js` and `terrain_filter_sheet.js` were behind
  a template condition. Each returns early when its markup or core is
  absent, so they joined the bundles unconditionally rather than splitting
  a bundle per condition. Scripts that stay behind a condition
  (`?layers=exploded`, the superuser editors) are tagged after the map
  bundle, as before.

## Adding a map module

Add the file to the right list at the right position. Do not tag it in the
template. `tests/public/templatetags/test_js_bundles.py` checks every member
exists and is listed once, and that each list renders as one contiguous run
in list order; `tests/public/test_map_script_order.py` still checks the
`MAP_BUNDLE` order Vitest boots.
