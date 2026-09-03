---
name: nav_implementation_spec
description: templates/includes/nav.html partial — back_url/back_label/season_trigger parameters, auth and staff dropdowns, sync badge, full-width layout
status: current
last-reviewed: 2026-09-03
---

# Navigation implementation spec

Single Django template partial at
[`templates/includes/nav.html`](../templates/includes/nav.html), included on
every public page via `public/base.html`.

**The partial's own `{% comment %}` docstring is the source of truth for its
parameters and usage.** This page carries only what the docstring can't: the
layout rationale, and the decisions about what deliberately isn't in the nav.
Earlier revisions of this doc embedded a copy of the markup, which silently
drifted from the template — don't reintroduce one.

## What it renders

Left to right:

1. Optional back-chevron link + a thin vertical divider (`back_url`).
2. The Snowdesk wordmark, always linking home. It renders at `text-lg`
   standalone and drops to `text-label` when sharing the row with a back link.
3. Optional right-aligned **Season** button (`season_trigger`), on bulletin
   pages only.
4. The right-side cluster: sync badge, then account auth, then staff admin.

### Right-side cluster

- **Sync badge** (SNOW-376) — a `[data-sync-badge]` pill, hidden at rest.
  `static/js/mutation_queue.js` reveals it with the live non-`failed`
  `queue:mutations` depth and swaps it to the `status-error` tokens once a
  queued mutation has permanently failed. It carries only `hidden` at rest;
  `inline-flex` is added by the script, so a count-zero badge never leaks a
  stray pill (SNOW-445). See [`docs/mutation-queue.md`](mutation-queue.md).
- **Authenticated account** — an avatar button (first letter of the email)
  opens a dropdown holding the offline-mode switch, "Settings" and "Sign
  out". Since SNOW-802/803 that is the whole menu: the region links and
  the list entries it once carried are map sheets now, and the
  `nav_subscriptions` context processor that fed the region links is gone
  ([`account-area-navigation-lives-in-the-nav-menu`](decisions/account-area-navigation-lives-in-the-nav-menu.md)).
- **Not authenticated** — a single "Sign in" button to `/account/sign-in/`,
  which itself carries the "Create an account" link.
- **Staff overlay** — a cog button opens an admin dropdown (Component library,
  Push demo, Edit map, Django admin). It is rendered *in addition to* the
  account avatar, so a staff user with an account sees both.

Both dropdowns are native `<details>` disclosures (SNOW-616), not scripted
ones. Opening, closing, Enter/Space and focus all work with JavaScript
disabled — which is what makes **Sign out** reachable without it, the one
control a user must be able to reach on a shared or borrowed device. The
inline script adds only click-outside and Escape-to-close; neither is
load-bearing.

## Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `back_url` | str | Optional. URL for the chevron-back link. Omit where "back" has no obvious target (home/map). |
| `back_label` | str | Optional. Defaults to a translated "Back" — prefer a destination-specific label ("Map") so the user knows where the arrow leads. |
| `season_trigger` | dict | Optional. The `season_header()` context — `{"season_label": "<NN/NN>"}`, or `None` before the season starts. When truthy a labelled "SEASON" button renders right-aligned. Bulletin pages only. (The partial's docstring calls this a bool; it is read as `season_trigger.season_label`.) |

## Usage per page

```html
{# Most pages (via public/base.html) — wordmark only #}
{% include "includes/nav.html" %}

{# Bulletin page — back to the map, plus the season trigger #}
{% include "includes/nav.html" with back_url=map_url back_label="Map" season_trigger=season_calendar %}
```

## Layout rationale (SNOW-445)

The `<nav>` spans the full page width so its `border-bottom` forms an
edge-to-edge rule, **and so does the inner row** — constrained only by its
`px-4` gutter. This deliberately no longer matches the 640px bulletin column:
capping the row at `max-w-narrow` left the wordmark floating at the left edge
of a centred column on wide screens, which read as a drift bug. The wordmark
now pins to the left viewport gutter and the auth cluster to the right.

## Deliberately absent

- **No hamburger menu and no secondary link list.** Everything in the bar is
  either wayfinding (wordmark, back) or account state.
- **No Help link.** Help lives in the footer
  (`apps/public/templates/public/_site_footer.html`) only — it was removed
  from the header to declutter the bar (SNOW-445).
- **No calendar button.** Earlier revisions took `calendar_region_id` /
  `calendar_partial_url` and rendered an HTMX calendar toggle here. That was
  replaced by the `season_trigger` button above; the calendar partial is now
  reached from the season overlay. See [`docs/calendar.md`](calendar.md).
