---
name: account-area-navigation-lives-in-the-nav-menu
description: The account area has no sub-nav, tab strip or hub heading — the vertical nav dropdown is its navigation and each section is a sibling page
status: current
last-reviewed: 2026-08-23
---

# Account-area navigation lives in the nav menu

**Decision.** The pages under `/account/` carry **no sub-navigation of their
own** — no tab strip, no segmented control, no group headings above the
content. Navigation between them is the subscriber dropdown in
`templates/includes/nav.html`. Each account section is its own page, listed
there: Settings today, and Favourites, Routes, Observations and
Subscriptions as SNOW-668, SNOW-677 and their successors split them out of
the hub. Adding a destination is adding one `<a>` to that list.

The hub at `/account/` carries an `sr-only` `<h1>` and no visible page
heading. Its sections are peer `_eyebrow` labels, the same primitive the
settings page uses for its groups.

**Why.** Two horizontal sub-navs were built for this area and both were
removed. SNOW-667 grouped two links under two eyebrow headings and spent
~180px of chrome above the `<h1>` to offer a choice of two destinations, with
a "Settings" group whose only entry was "Settings"; it was cut before
shipping. SNOW-705 replaced it with a flat tab strip, which fixed the chrome
but not the premise.

The premise is the problem. A horizontal strip is **width-bound**, and the
budget is small: at 375px the account column leaves ~343px, and five short
English labels measure ~330px of it. That is not a comfortable fit — swapping
one label for "Observations", the word in SNOW-677's own URL, takes the row
to 366px and overflows. So a horizontal strip forces every future account
page to be *named to fit* rather than named to be clear, and quietly makes a
layout constant into a content constraint.

The nav dropdown has none of that. It is vertical, so it expands down a page
that scrolls rather than across a viewport that does not; it is already the
way into the area from everywhere else on the site; and it costs nothing
above the content of the pages it points at. A second surface duplicating it
was never earning its keep — least of all on a phone, where the strip's cost
is highest and the menu is one tap away.

There was also nothing to navigate *between*. The area has two pages, one of
which — the hub — summarises nothing: it is two lists of saved things, so
"Overview" named the slot rather than the contents. The page audit of
2026-08-23 reached the same read independently: *"the account area is a
settings screen with a hub in front of it."* SNOW-705's own ticket named the
trap — *"Building the container before its contents was the mistake"* — and
the honest answer to "grouped or flat?" turned out to be *neither, yet*.

**Consequences.**

- **Do not add a sub-nav, tab strip or breadcrumb to `/account/` pages.**
  `tests/accounts/test_account_layout.py::TestNoSubNav` fails if one appears.
  A third attempt will look like an improvement; read this file first.
- **The nav dropdown's Settings entry is load-bearing**, not the placeholder
  SNOW-667 labelled it. It is the only route to `/account/settings/`.
- **New account sections are new pages plus one menu entry.** They do not
  need a navigation design, a label-length budget, or a decision about
  grouping. SNOW-677 is free to call its page "Observations".
- **No page-level heading is added to the hub** to fill the gap the strip
  left. Nothing there summarises anything; the menu named the destination on
  the way in.
- If the menu itself grows past roughly seven or eight entries — subscribed
  regions plus every account section plus sign-out — it becomes long enough
  to need grouping, and *that* is the point to revisit this. Grouping a
  vertical list is cheap; it was only ever expensive horizontally.
