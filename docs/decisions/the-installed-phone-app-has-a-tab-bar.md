---
name: the-installed-phone-app-has-a-tab-bar
description: Installed phone app tab bar (Map · Today · Saved · Help), standalone under 640px, tab panels, Saved chips, Help opens the ? menu
status: current
last-reviewed: 2026-11-08
---

# The installed phone app has a tab bar

**Decision.** In the installed app on a phone — `(display-mode: standalone)
and (max-width: 639.98px)` — every page carries a bottom tab bar of four
tabs: **Map · Today · Saved · Help** (SNOW-1476). Everywhere else (a phone
browser, a tablet, a desktop) nothing changes: the bar is rendered but
`display: none`.

- **Map** closes whatever tab panel is open and gives the map back.
- **Today** opens the existing Today panel, with the next few trips added.
- **Saved** opens the favourites, routes or report sheet, whichever was last
  used; a chip row under each header (`_saved_chips.html`) swaps between
  them in place: the card stays put and only its content changes.
- **Help** opens the map's own "?" menu (`_map_help_menu.html`, through
  `window.pwaMapHelpMenu`), the same card as on the web, anchored above the
  bar on the right. In this app only it gains **Map key**, which opens the
  (i) legend card (`window.pwaMapLegend`), and the footer's legal line.
- **Offline areas** moves from the hidden roundel column into the layers
  menu (`_map_layers_offline_row.html`), in this app only.

Items shown only in this app carry `.app-only`, which a negated copy of the
gate hides everywhere else. The account stays in the header's avatar menu,
unchanged from the web.

Today and Saved are **tab panels**: full height between the header and the
bar, no detents, opened and closed by their tab. Help is the "?" card,
which rises over a tab panel rather than cross-fading with it. On the map the tabs are
buttons (`static/js/app_tabbar.js`); on every other page they are links to
`/map/?panel=today|saved|help`, which the script opens and strips from the
URL. In that mode the bar replaces the site footer, the Today roundel, the
map-control column, and the "?" and (i) roundels, which the tabs and the
"?" menu make redundant; zoom and compass drop to the bottom row. Signed out all four tabs still show: Today and Saved
open the favourites sheet's own sign-in prompt.

**Why.** On a phone the map's lists sat behind a column of roundels that
read as tools, not places, and the account behind an avatar most visitors
never open. The installed app is where people return daily; there the bar
gives the four things they come back for one fixed place under the thumb.
Hugo chose this set from three options on 8 Nov 2026
(the exploration is on the SNOW-1476 ticket).

The fourth tab was **You** at first: the account, Offline areas, Resorts,
Help and the legal links. On staging Hugo found it an odd selection of
"your data" — routes and observations were in Saved, Trips and Resorts in
You — and moved the avatar back to the header and Help into the bar, with
the map key in it (8 Nov 2026). A first Help panel of its own was rejected
the same day for the existing "?" menu: one help surface, not two. Trips
becomes a Saved chip in its own change; Offline areas went to the layers
menu.

Installed-only, because a browser tab already has its own bottom chrome
and the site's header and footer, and because the change can be judged by
the people who installed it before it reaches everyone. Sheets over the map
rather than separate pages, because the map stays the app's home and the
existing sheets already hold the lists.

**Consequences.**

- The bar is gated in CSS alone. Tests parse `src/css/main.css` and
  `static/css/map.css` for the gate; no test can put a browser in
  standalone mode, so screenshots force it by stripping `display-mode`.
- A tab panel is a fourth panel kind beside the three in
  [`docs/design-system.md`](../design-system.md#map-panels-what-opened-it-decides-its-shape).
- [`account-area-navigation-lives-in-the-nav-menu`](account-area-navigation-lives-in-the-nav-menu.md)
  holds unchanged: the avatar menu is the account's navigation in this app
  as everywhere else.
- Widening the bar to phone browsers is a CSS change to one media query,
  plus a check that the browser's own chrome and the bar do not stack.
