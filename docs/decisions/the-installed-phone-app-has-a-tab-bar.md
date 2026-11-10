---
name: the-installed-phone-app-has-a-tab-bar
description: Installed phone app tab bar (Map · Saved · Help), standalone under 640px, Saved tabs and + roundel, .app-hidden, Help opens ? menu
status: current
last-reviewed: 2026-11-10
---

# The installed phone app has a tab bar

**Decision.** In the installed app on a phone — `(display-mode: standalone)
and (max-width: 639.98px)` — every page carries a bottom tab bar of three
tabs: **Map · Saved · Help** (SNOW-1476; Today was a fourth until
SNOW-1479, below). Everywhere else (a phone
browser, a tablet, a desktop) nothing changes: the bar is rendered but
`display: none`.

- **Map** closes whatever tab panel is open and gives the map back.
- **Saved** opens Places, Routes, Trips or Observations, whichever was last
  used (Places by default); four icon tabs in each header
  (`_saved_tabs.html`) swap between them in place: the card stays put and
  only its content changes. Only the selected tab shows its name.
- **Help** opens the map's own "?" menu (`_map_help_menu.html`, through
  `window.pwaMapHelpMenu`), the same card as on the web, anchored above the
  bar on the right. In this app only it gains **Map key**, which opens the
  (i) legend card (`window.pwaMapLegend`), and the footer's legal line.
- The downloads sheet, whose roundel this app hides, opens from the layers
  menu's **Downloaded areas** row (`_map_layers_downloads_row.html`): its
  left half opens the sheet, its switch shows the areas on the map, as on
  Steep terrain's row. The row is the same on the web.

Items shown only in this app carry `.app-only`, which a negated copy of the
gate hides everywhere else; items hidden only in this app carry
`.app-hidden`, which the gate itself hides (SNOW-1479). The account stays in
the header's avatar menu, unchanged from the web except that its Trips
entry is `.app-hidden`: Trips is a Saved tab here.

Saved is a **tab panel**: full height between the header and the
bar, no detents, opened and closed by their tab. Help is the "?" card,
which rises over a tab panel rather than cross-fading with it. On the map the tabs are
buttons (`static/js/app_tabbar.js`); on every other page they are links to
`/map/?panel=saved|help`, which the script opens and strips from the
URL; an older `?panel=today` opens Saved on Places. In that mode the bar replaces the site footer, the Today roundel, the
map-control column, and the "?" and (i) roundels, which the tabs and the
"?" menu make redundant; zoom and compass drop to the bottom row. Signed out all three tabs still show: Saved opens
the favourites sheet's own sign-in prompt.

**Saved's header (SNOW-1479).** In the app a Saved sheet's header is just
the four tabs and a **+** roundel (`_saved_add_roundel.html`); its title,
subtitle, section label and the foot's add button the + replaces are
hidden, and its × is `.app-hidden`. Since SNOW-1481 the same Saved panel
opens from the star on every other screen
([`saved-is-one-panel-on-every-screen.md`](saved-is-one-panel-on-every-screen.md)),
so only the × is app-only, and the Saved logic lives in `saved_panel.js`
(`pwaSavedPanel`), which `app_tabbar.js` calls. Each + does its list's add: **Places** drops a pin,
**Routes** opens the Find / Create / Upload menu, **Trips** opens a menu
headed "Plan a trip from" listing the reader's own routes, each to
`trips:new?route=<uuid>` (`_trip_route_picker.html`, filled by
`trips_panel.js` from `routes:geojson`), and **Observations** starts a
report. Trips' + used to switch Saved to Routes, which confused people; the
picker keeps that only for a reader with no routes, whose one item is
`data-saved-go="routes"`. Places, Routes and Observations keep
their "Show on the map" switch.

- **Places** (`favourites:places`, `build_places`) is Today folded into
  Saved: every region with its danger, the pinned resorts under it with
  their weather, and the dropped pins with their height, pins outside
  every region under "Elsewhere". A region listed only because of a resort
  or a pin has no "…", since there is no region pin to share or delete.
- **Routes**: a recorded route shows its date; a shared one says "Shared".
- **Trips** (`trips:panel_list`, `trips_panel.js`): upcoming soonest
  first, then past. An organiser's row has Share, Rename and Delete; a
  participant's row has no menu.
- **Observations**: the row's Delete sits behind a "…" alone, an exception
  to the one-bare-icon rule written in
  [`docs/design-system.md`](../design-system.md#interaction-principles).
- The Today alert dot moves to the Saved tab.

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
became a Saved tab in its own change (SNOW-1479).

Today went the same way (SNOW-1479): its regions and resorts were the
same places as Saved's pins seen for today, so a tab of its own split one
list in two. Places holds both, and the date row's sun still opens the
Today panel on the web. A separate "Offline areas" row
under Downloaded areas named the same thing twice, so the two became one
row.

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
