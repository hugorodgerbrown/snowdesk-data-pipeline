---
name: the-installed-phone-app-has-a-tab-bar
description: Installed phone app bottom tab bar (Map · Today · Saved · You), standalone under 640px, tab panels, Saved chips, You panel
status: current
last-reviewed: 2026-11-08
---

# The installed phone app has a tab bar

**Decision.** In the installed app on a phone — `(display-mode: standalone)
and (max-width: 639.98px)` — every page carries a bottom tab bar of four
tabs: **Map · Today · Saved · You** (SNOW-1476). Everywhere else (a phone
browser, a tablet, a desktop) nothing changes: the bar is rendered but
`display: none`.

- **Map** closes whatever tab panel is open and gives the map back.
- **Today** opens the existing Today panel, with the next few trips added.
- **Saved** opens the favourites, routes or report sheet, whichever was last
  used; a chip row under each header (`_saved_chips.html`) swaps between
  them with a cross-fade.
- **You** opens a new panel (`_you_surface.html`): the account entries
  (shared with the avatar menu through `_account_menu_items.html`), Offline
  areas, Resorts, the footer and Sign out.

The four panels are **tab panels**: full height between the header and the
bar, no detents, opened and closed by their tab. On the map the tabs are
buttons (`static/js/app_tabbar.js`); on every other page they are links to
`/map/?panel=today|saved|you`, which the script opens and strips from the
URL. In that mode the bar replaces the site footer, the avatar menu, the
header's Sign in, the Today roundel and the map-control column, which the
panels make redundant. Signed out all four tabs still show: Today and Saved
open the favourites sheet's own sign-in prompt.

**Why.** On a phone the map's lists sat behind a column of roundels that
read as tools, not places, and the account behind an avatar most visitors
never open. The installed app is where people return daily; there the bar
gives the four things they come back for one fixed place under the thumb.
Hugo chose this set from three options on 8 Nov 2026
(the exploration is on the SNOW-1476 ticket).

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
  still holds: the You panel lists the same entries from the same partial,
  so a new account page still adds one `<a>` in one place.
- Widening the bar to phone browsers is a CSS change to one media query,
  plus a check that the browser's own chrome and the bar do not stack.
