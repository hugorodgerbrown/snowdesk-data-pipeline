---
name: help-illustrations-are-live-mocks
description: /help/ illustrations render real partials from apps/public/component_previews.py, not screenshots; inert wrappers, namespaced ids
status: current
last-reviewed: 2026-08-28
---

# Help illustrations are live mocks, not screenshots

**Status**: Accepted (SNOW-744)

## Context

`/help/` explains sixteen surfaces — the layers menu, the season heatmap,
the four panels that manage a user's own data — and until SNOW-744 it
showed none of them. A reader learning what a split calendar tile means
had to hold a sentence in their head, go and find the tile, and check they
had matched the description to the right thing.

Two ways to fix that: paste screenshots, or render the real component from
a synthetic context.

## Decision

Illustrate each topic by rendering the **real partial** for the surface it
describes, fed by a hand-built context in
[`apps/public/component_previews.py`](../../apps/public/component_previews.py).
No screenshots.

The contexts are built in memory. `/help/` issues no database queries and
must keep issuing none.

## Why not screenshots

Because this page has already demonstrated the failure mode. The review
that produced SNOW-744 found four claims on it that had quietly stopped
being true — the offline-cache promise, the offline banner's "two clocks",
the Observations page's time-rounding, and the season calendar described as
"a month grid". Prose rots when the code moves and nothing tells you.

A screenshot rots the same way and is harder to catch: there is no linter
for a PNG, no test that fails when the component changes, and nothing in a
diff that shows a picture has gone out of date. It would also need light,
dark, mobile and per-locale variants, each a binary blob in git.

A live mock inherits the design tokens, the theme and the translations, and
changes when the component changes. When it breaks, it breaks visibly.

## Why not `_component_fixtures.py`

The staff-only component library at `/_components/` already renders about
fifty components from synthetic fixtures, so reusing that module was the
obvious first thought. It was rejected on two grounds.

The module states in its own docstring that nothing in it is a public
import surface, and `/help/` is public. Importing it would break that
contract for the sake of reuse.

The reuse would also have been thin. The library's season-calendar fixture
is a curated thirteen-week grid covering every cell state, including the
ALBINA elevation and AM/PM band splits, because its job is exhaustive
coverage for a designer iterating on tile appearance. The help page's job
is to teach, so it wants a short grid a reader takes in at a glance. Two
different datasets serving two different purposes; what they genuinely
share is the `SeasonGrid` / `SeasonCell` dataclasses, and those already
live in the public `apps.public.season_calendar`.

So `component_previews.py` builds its own. `_component_fixtures.py` is
untouched and its boundary holds.

## Consequences

**Illustrations are `inert`.** They render real switches, buttons and a
close X, every one of them dead because nothing binds a listener on this
page. The wrapper in `includes/_collapsible_panel.html` carries `inert`
alongside `aria-hidden`: aria-hidden alone would leave focusable controls
in the tab order, which is the focusable-inside-aria-hidden fault. The
prose must therefore stand on its own, because a screen-reader user gets
the paragraph and nothing else.

**Ids must be namespaced.** The real panels' switches are how `map.js`
finds what it drives (`#map-favourites-overlay-toggle` and friends). An
illustration must never answer to one of those names, so
`component_previews.py` gives each a `help-illustration-…` id.

**A component whose styles are not in `output.css` cannot be illustrated
here.** `/help/` loads that stylesheet alone. The season scrubber has a
perfectly good demo partial, but every rule that gives it a track, a thumb
and a date pill lives in `static/css/map.css`; rendered on `/help/` it
collapses into a row of loose buttons over a zero-height track. Pulling the
map's stylesheet onto a text page to decorate one panel is the wrong trade
against the Lighthouse budget, so that topic is unillustrated and the
scrubber moved to SNOW-745 with the other surfaces that need a demo form of
their own.
