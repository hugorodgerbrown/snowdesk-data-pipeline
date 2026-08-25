---
name: account-area-navigation-lives-in-the-nav-menu
description: No sub-nav or tab strip on /account/ pages — the nav.html dropdown is the area's only navigation, and a new page needs a menu entry
status: current
last-reviewed: 2026-08-25
---

# Account-area navigation lives in the nav menu

**Decision.** The pages under `/account/` carry **no sub-navigation of their
own** — no tab strip, no segmented control, no group headings above the
content. Navigation between them is the subscriber dropdown in
`templates/includes/nav.html`. Each account section is its own page, listed
there — Subscriptions, Favourites, Routes, Observations, Settings, in that
order, since SNOW-668 completed the list. Adding a destination is adding one
`<a>` to it, plus an assertion in `tests/public/test_nav_partial.py`.

Every account page carries a visible `<h1>` naming itself. The hub's was
`sr-only` between SNOW-705 and SNOW-668 for a reason that has since gone:
it held subscriptions *and* favourites, summarised neither, and so had no
honest name. One list per page restored one.

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

There was also nothing to navigate *between* at the time. The area had two
pages, one of which — the hub — summarised nothing: it was two lists of
saved things, so "Overview" named the slot rather than the contents. (It is
five pages now, and the conclusion holds: the menu simply lists five.) The
page audit of 2026-08-23 reached the same read independently: *"the account area is a
settings screen with a hub in front of it."* SNOW-705's own ticket named the
trap — *"Building the container before its contents was the mistake"* — and
the honest answer to "grouped or flat?" turned out to be *neither, yet*.

**Consequences.**

- **Do not add a sub-nav, tab strip or breadcrumb to `/account/` pages.**
  `tests/accounts/test_account_layout.py::TestNoSubNav` fails if one appears.
  A third attempt will look like an improvement; read this file first.
- **Every entry in the dropdown is load-bearing**, not a placeholder — each
  is the only route to the page it names. SNOW-677 and SNOW-713 proved the
  converse the hard way: `/account/observations/` and `/account/routes/`
  both shipped mounted, covered by passing tests and reachable only by
  typing the URL, because those tests reverse the URL rather than following
  a link. **A new account page is not done until it has a menu entry and an
  assertion for that entry** in `tests/public/test_nav_partial.py`
  (SNOW-668).
- **A flag-gated page needs a flag-gated entry.** `my_routes` answers 404
  when `routes` is inactive, so the Routes entry is wrapped in
  `routes_visible`, injected by `apps.accounts.context_processors` because
  `nav.html` renders on pages that pass no context of their own.
- **New account sections are new pages plus one menu entry.** They do not
  need a navigation design, a label-length budget, or a decision about
  grouping.
- **Each page names itself in a visible `<h1>`.** The hub's was hidden only
  while it held two unrelated lists (see above); that is not a rule for the
  area.
- The menu is now at or near the size this decision named as the point to
  revisit: up to three subscribed regions, five account entries and sign
  out. Grouping a vertical list is cheap — it was only ever expensive
  horizontally — so revisiting means adding rules, not rebuilding a strip.
