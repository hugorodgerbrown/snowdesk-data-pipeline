---
name: saved-is-one-panel-on-every-screen
description: Saved panel on every screen — star opens Places · Routes · Trips · Observations, saved_panel.js, docked left, binoculars report
status: current
last-reviewed: 2026-11-10
---

# Saved is one panel on every screen

**Decision.** Outside the installed phone app, the star roundel
(`#favourite-add-btn`) opens the same **Saved** panel the app's Saved tab
does: Places · Routes · Trips · Observations, with a + (SNOW-1481).

- **One controller.** `static/js/saved_panel.js` (`window.pwaSavedPanel`)
  owns Saved: the four sheets (favourites, routes, trips, report), the tab
  last used for the session (Places at first; Places always for a visitor,
  whose sheet asks them to sign in), the `saved-tab` radios and
  `data-saved-go`. It owns the star's click, and sets the star's
  `aria-expanded` while Saved is open; `static/css/map.css` then shows the
  filled star. `app_tabbar.js` calls it for its Saved tab.
- **One header.** The Saved tabs, the + and the Trips route picker draw on
  every screen. A Saved sheet's title, context line, section label and
  add-button foot are hidden on every screen; only the × is `.app-hidden`,
  since in the app the tab bar closes the sheet and in a phone browser the
  × is how you close a panel that covers the star.
- **Where it opens.** On a desktop Saved docks left under search
  (`side="left"`); in a phone browser it slides up to at most ¾ of the
  screen (`75dvh`), with no detents. It is a roundel panel (design-system
  rule 6). The app's full-height gate is unchanged.
- **One list for Places.** The favourites sheet always lists
  `favourites:places`. The pins-only `favourites:list` is retired, and with
  it the cached paint of that list: offline, Places says it couldn't be
  loaded, as Today does.
- **The binoculars start a report.** One tap opens the report form
  (`report.js`), in a sheet marked `data-report-direct` so Saved does not
  count it as Saved › Observations; the reader's own reports are in Saved.

**Why.** The app's Saved put the reader's places, routes, trips and
observations in one place, and a desktop or phone-browser reader had four
roundels for the same lists, one of them (pins) a subset of what Places now
shows. One panel everywhere means one thing to learn. Docking left breaks
"a surface opens beside its trigger"
([`a-panel-opens-beside-what-opened-it.md`](a-panel-opens-beside-what-opened-it.md));
Hugo chose it so Saved sits in the column that holds the reader's own
things on a desktop, where the app's Saved sits on a phone. The binoculars
became one tap because a report is what that roundel is for; the list
behind it moved to Saved, where the other lists are.

**Consequences.**

- The sun and route roundels still open their own sheets (Today, and the
  routes sheet, which now shows the Saved tabs); removing them is SNOW-1484.
- The star carries no `data-overlay-shown`: Saved is four lists and no one
  layer (`map_roundel_overlay_state.js`).
- A new Saved list joins in three places: `SAVED_SHEET_IDS` in
  `saved_panel.js`, `_saved_tabs.html`, and the sheet selectors in
  `map.css`.
