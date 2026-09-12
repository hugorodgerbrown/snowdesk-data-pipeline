---
name: testing-scenarios
description: Manual test scenarios — bulletin, map, search, accounts, PWA install/update/kill-switch, offline downloads, coverage, end-user offline run
status: current
last-reviewed: 2026-09-07
---

# User Testing Scenarios -- Snowdesk

> **Prerequisites**
>
> 1. Django dev server running: `uv run python manage.py runserver` at http://localhost:8000
> 2. Mailpit running on localhost:1025 (web UI at http://localhost:8025)
> 3. Tailwind CSS watcher running: `npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch`
> 4. Database migrated: `uv run python manage.py migrate`
> 5. Dataset seeded: `uv run python manage.py loaddata eaws_CH && uv run python manage.py import_resorts --commit && uv run python manage.py seed_test_data --all --commit`
>    These commands load the region/resort reference data and seed all the
>    bulletin and day-rating data needed to navigate every
>    scenario below. The
>    canonical preview URL is `/ch-4115/martigny-verbier/2026-04-08/`.

> **Reading this as a page**
>
> `bin/render-testing-scenarios` renders this file as one navigable HTML
> page — an index of every section and scenario, an anchor on every heading,
> and every `http://localhost:8000` URL turned into a link you can click
> while the dev server is running. This markdown stays the source of truth;
> the page is a projection of it, so re-render after editing.
>
> ```bash
> uv run python bin/render-testing-scenarios --output /tmp/scenarios.html
> ```

---

## Public Bulletin Site

### Scenario 1: View the homepage (the map, with its intro overlay)

**Goal**: Verify the homepage loads as the interactive map behind a
dismissable landing overlay. There is no separate marketing page — the map
*is* the homepage (SNOW-314).

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Clear `localStorage` for the site, then navigate to http://localhost:8000/ | The full-frame map loads, with the `#home-intro` card centred over it |
| 2 | Read the intro card | It shows "Welcome to Snowdesk", a tagline naming SLF (Switzerland), ALBINA (Austria, Italy) and Météo-France (France), and a "Register" inline link |
| 3 | Look at the page chrome | A thin top nav bar with a "Snowdesk" wordmark (no back link) sits above the map |
| 4 | Locate the intro card's controls | A "×" close button (top-right of the card) and an "Explore the map" button at the foot of it |
| 5 | Note the pre-selected region | CH-4115 (Martigny/Verbier) is already selected, so the readout chip and breadcrumb are populated on first paint (SNOW-342) |

Out of season (today past the season end) the card also carries an
off-season note naming the archived season's start month.

### Scenario 2: View a random sample bulletin

**Goal**: Verify `/examples/random/` serves a random bulletin inline.

> Reach this URL directly — no template links to it. The homepage is the map,
> and its intro overlay carries only the Register link and the dismiss button.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/examples/random/ | A bulletin page loads (URL stays the same, no redirect) |
| 3 | Verify the bulletin page content | Page shows a region name as heading, a date label ("Today" or a formatted date), and an "issued HH:MM UTC" timestamp |
| 4 | Check for danger level | A danger level indicator is visible (e.g. "Level 2 -- Moderate") with a coloured badge |
| 5 | Refresh the page (F5 / Cmd+R) | A different region's bulletin loads (URL remains `http://localhost:8000/examples/random/`) |

### Scenario 3: Navigate between bulletin dates using prev/next links

**Goal**: Verify day-based navigation works on the bulletin detail page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/CH-1221/grindelwald/2026-04-10/ | Bulletin page loads for Grindelwald on 10 Apr 2026 |
| 2 | Verify the date in the header | Header shows "Fri 10 Apr 2026" (not "Today") |
| 3 | Click the next-day navigation link (right arrow) | Browser navigates to the next available date (e.g. `/CH-1221/grindelwald/2026-04-11/`); header date updates accordingly |
| 4 | Click the previous-day navigation link (left arrow) | Browser navigates back to `/CH-1221/grindelwald/2026-04-10/`; header shows "Fri 10 Apr 2026" again |

### Scenario 4: View today's bulletin via the region shortcut URL

**Goal**: Verify that visiting a region ID without a slug redirects to the canonical bulletin URL.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/CH-4222/ | Browser redirects to `http://localhost:8000/CH-4222/zermatt/` (or similar slug derived from the region name) |
| 2 | Verify the bulletin page | Page shows the region name (Zermatt area) and today's date label shows "Today" |

### Scenario 5: View a random bulletin filtered by danger level

**Goal**: Verify the examples/category route finds a matching bulletin.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/examples/category/considerable/ | Browser redirects to a bulletin detail page for a region where the danger level is "Level 3 -- Considerable" |
| 2 | Verify the danger level on the page | The displayed danger level shows "Considerable" with an orange colour badge |
| 3 | Navigate to http://localhost:8000/examples/category/low/ | Browser redirects to a bulletin with danger level "Level 1 -- Low" |

### Scenario 6: View the season archive for a region

**Goal**: Verify the full-season bulletin grid loads for a region.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/CH-7114/season/ | Season page loads with heading showing the region name |
| 2 | Verify the panel count | A subtitle shows the number of bulletins (e.g. "12 bulletins this season") |
| 3 | Verify the grid layout | Bulletin panels are displayed in a responsive grid (single column on mobile, up to three columns on wide screens) |
| 4 | Verify each panel card | Each card shows a date, danger level indicator, and avalanche problem tags |

### Scenario 7: Access a bulletin for a non-existent region

**Goal**: Verify the application returns a 404 for an unknown region ID.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/CH-9999/ | Browser shows a 404 Not Found page |

### Scenario 8: View a bulletin when no data exists for the requested date

**Goal**: Verify graceful handling when no bulletin data is available.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/CH-1221/grindelwald/2020-01-01/ | Bulletin page loads but shows no bulletin content (no danger level, no hazards); the region name heading is still visible |

### Scenario 9: Access an invalid danger level category

**Goal**: Verify a 404 is returned for an unrecognised danger level slug.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/examples/category/extreme/ | Browser shows a 404 Not Found page |

---

## Top Navigation

### Scenario N1: Persistent nav bar appears on every public page

**Goal**: Verify `templates/includes/nav.html` is rendered consistently and
the wordmark always links home.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ | Nav bar visible at the top with a "Snowdesk" wordmark (18px); no back link |
| 2 | Navigate to http://localhost:8000/ (the map) | Same nav bar visible above the map frame; no back link |
| 3 | Navigate to http://localhost:8000/CH-4115/verbier/ | Nav bar shows a left-chevron + "Map" link, a thin vertical divider, and the "Snowdesk" wordmark at a smaller size (15px) |
| 4 | Navigate to http://localhost:8000/CH-4115/season/ | Nav bar again shows "← Map" and the wordmark |
| 5 | Navigate to http://localhost:8000/CH-4115/ (random/recent bulletins) | Nav bar again shows "← Map" and the wordmark |
| 6 | Click the "Snowdesk" wordmark from any of the pages above | Browser navigates to http://localhost:8000/ |
| 7 | From a bulletin page, click the "← Map" back link | Browser navigates to http://localhost:8000/ |

### Scenario N2: Nav border spans the full viewport

**Goal**: Verify the nav's bottom border stretches edge-to-edge while the
inner content stays aligned with the body copy.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/CH-4115/verbier/ on a wide desktop window | The `<nav>` bottom border runs across the full browser width; the "← Map" link and wordmark sit within a 640px-wide inner column that matches the bulletin body copy |
| 2 | Resize the window down to ~390px (phone width) | The nav keeps the chevron, label, divider, and wordmark on a single row with no wrap |

---

## Map Page

### Scenario M1: Load the map and see today's choropleth

**Goal**: Verify the map page renders all regions, colours them by today's
danger rating, and shows the legend.

**Preconditions**: Bulletins for today have been fetched
(`uv run python manage.py fetch_bulletins --source slf --date $(date +%Y-%m-%d) --commit`)
and regions/resorts fixtures are loaded.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ | MapLibre basemap of Switzerland renders centred on the Alps; Swiss region polygons are overlaid and filled in danger-level colours |
| 2 | Locate the legend | A "Today" legend is visible with four swatch rows: "Low (1)" green-yellow, "Moderate (2)" yellow, "Considerable (3)" orange, "High (4)" red |
| 3 | Locate the hint text | Footer hint reads "Pinch to zoom · tap a region" |
| 4 | Check regions without today's bulletin | Rendered in grey (no_rating colour) — not missing |
| 5 | Zoom in past zoom level ~8.5 | Region labels appear (region names rendered in the polygon centres); zoom back out and they disappear |

### Scenario M2: Tap a region to open the bottom sheet

**Goal**: Verify clicking a region surfaces today's danger rating, linked
resorts, and a working CTA to the bulletin page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On http://localhost:8000/, click a region with a bulletin (e.g. Verbier / CH-4115) | A bottom sheet slides up from the page bottom |
| 2 | Read the sheet title | Shows the region name (e.g. "Verbier") |
| 3 | Read the sheet body | Shows today's danger rating and any linked resort names |
| 4 | Locate the CTA | "Read today's bulletin →" link visible at the bottom of the sheet |
| 5 | Click the CTA | Browser navigates to the region's bulletin page for today (e.g. `/CH-4115/verbier/`) |
| 6 | Return to / and click a different region | Sheet content updates; the newly selected region's outline thickens and darkens |
| 7 | Click the × close button on the sheet | Sheet dismisses; the region's selection outline is removed |

### Scenario M3: Regions with no bulletin render as no_rating

**Goal**: Verify regions absent from `/api/ratings/` show as grey
and the sheet reflects that.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the Django shell, delete today's bulletins for one region (e.g. `RegionBulletin.objects.filter(region__region_id='CH-4115', ...)`) | Bulletins removed |
| 2 | Reload http://localhost:8000/ | That region renders in grey |
| 3 | Click the region | Sheet opens; danger rating shows as "no rating" / unavailable |

### Scenario M4: Debug mode reveals region IDs

**Goal**: Verify `?debug=1` or pressing `d` on the page reveals the debug pill
and region IDs in the sheet.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/?debug=1 | Map loads; a small "debug · press d" pill visible top-right |
| 2 | Click a region | Sheet shows the SLF region identifier (e.g. "CH-4115") in a monospace debug line beneath the region name |
| 3 | Navigate to http://localhost:8000/ (without the debug flag) | Debug pill is hidden |
| 4 | Press `d` on the keyboard | Debug pill appears and the sheet's region-id line becomes visible |

---

### Scenario M5: Switch on the Weather overlay

**Goal**: Verify the map's Weather overlay draws a condition icon and a
temperature/altitude label at each public location, and that the symbols
follow the scrubbed date.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ and open the layers menu | The "Conditions" section is open (the one section that is, on a first visit) and holds a "Weather" row, unchecked |
| 2 | Click the Weather row | Row becomes checked; condition icons appear across the map, each labelled with the day's max temperature and the station's altitude on the line below |
| 3 | Zoom out below zoom 7 | The symbols disappear entirely — a condition icon per station across a whole country is a texture, not information |
| 4 | Zoom back in to a valley with a village and a peak close together | At low zoom only one symbol shows per cluster, and it is the LOWER station's; both appear once zoomed past ~11 |
| 5 | Scrub the timeline forward a day | The icons and temperatures change without a page reload and without a new network request |
| 6 | Scrub back to a date before today | The symbols disappear — the feed is forecast-only, and there is nothing to draw for a past day |
| 7 | Reload the page | The Weather row is still checked and the symbols come back |

---

## Map Search

### Scenario MS1: Search for a region by name

**Goal**: Verify the autocomplete finds a Swiss avalanche region by name.

**Preconditions**: the map page has fully loaded (regions + resorts + summaries).

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On http://localhost:8000/, click into the search input placeholder "Find a region or resort" | Input receives focus |
| 2 | Type `Graubünden` | A dropdown appears below the input with up to eight results; a row for "Graubünden" is visible with a "Region" badge |
| 3 | Click the "Graubünden" row | Dropdown closes; the map pans/zooms to the region and its bottom sheet opens with today's rating |

### Scenario MS2: Search for a resort and land on its parent region

**Goal**: Verify resort hits route to the region sheet, not a separate page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the map search box, type `Verbier` | Results dropdown shows a "Verbier" row with a "Resort" badge and "Les 4 Vallées" (or similar region name) as secondary text |
| 2 | Click the row | Dropdown closes; the map pans to CH-4115 and the bottom sheet opens showing Verbier listed among the region's resorts |

### Scenario MS3: Diacritic-insensitive matching

**Goal**: Verify the search matches regardless of accented characters.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the map search box, type `evolene` (no accent) | Results include "Évolène" with its diacritics intact |
| 2 | Clear and type `graubunden` (no umlaut) | Results include "Graubünden" |

### Scenario MS4: Region and resort with the same name disambiguated by badge

**Goal**: Verify that when a resort shares its name with its parent region,
both hits appear and the badge makes the distinction obvious.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the map search box, type `Davos` | Two rows appear: one "Davos" with a "Region" badge, and one "Davos" with a "Resort" badge |
| 2 | Hover/scroll the results | The badges are visually distinct (different colour/shape) so the two rows can't be confused |

### Scenario MS5: Keyboard navigation of results

**Goal**: Verify the dropdown is operable without a mouse.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Focus the search input and type `Zermatt` | Results dropdown opens with the first row highlighted as the user moves down |
| 2 | Press the Down arrow key | First result becomes active (visually highlighted) |
| 3 | Press Down again | Active row moves to the next result |
| 4 | Press Enter | Dropdown closes; the map selects the active result's region and opens the sheet |
| 5 | Re-focus the input, type a query, then press Escape | Dropdown closes without any selection |

### Scenario MS6: Empty state and no matches

**Goal**: Verify the dropdown handles empty / unmatched queries gracefully.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Click into the search input and clear any existing text | Dropdown is hidden |
| 2 | Type `xyznonexistent` | Dropdown does not open (no results, no error message shown) |

### Scenario MS7: "Explore the map" dismisses the intro overlay

**Goal**: Verify the intro overlay's CTA is a *dismiss* control, not a link —
the map is already mounted behind it — that the dismissal persists, and that
the "?" roundel brings the card back rather than jumping straight to the tour.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Clear `localStorage`, navigate to http://localhost:8000/ | Map loads with the `#home-intro` card over it |
| 2 | Click "Explore the map" | The card clears in place. **No navigation occurs** — the URL stays `http://localhost:8000/` and the map is not reloaded. The map-help coachmark tour then opens (SNOW-535) |
| 3 | Inspect `localStorage` | `snowdesk.home.intro` is set to `dismissed` |
| 4 | Reload the page | The map loads with no intro card |
| 5 | Repeat from step 1, but click the "×" close button (or press Escape) | The card dismisses and persists the same way — but the map-help tour does **not** open. That extra step is what distinguishes the CTA from the "×" |
| 6 | With the card dismissed, click the "?" roundel in the bottom-left stack | The welcome card comes back, over whatever the map was showing; any other open panel (layers, legend, a sheet) closes. `snowdesk.home.intro` is still `dismissed` |
| 7 | Click "Explore the map" | The card clears and the coachmark tour opens on step 1 — the same route in as step 2, which is the only route in on this page |

To bring the overlay back without clearing `localStorage`, load
`http://localhost:8000/?intro=1` — it forces the panel open, survives a server
round-trip (unlike `#about`), and is stripped from the address bar on dismissal
so the panel stays dismissed across a reload. It is the handle to use in QA,
screenshots and bug reports.

---

## Accounts and Pins

Since SNOW-795 a saved region is a **region pin**, not a subscription
(nothing ever sent a bulletin). Since SNOW-814 it is listed in the **region
+ date panel** the map's readout chip opens, not in the pins sheet: that
sheet lists places, and a region has no coordinate, no name of the user's
own and no forecast, so none of its affordances reached one. The pin
control and the list of what you pinned are now the same surface. The
account area is `/account/settings/` alone; `/account/` and the old list
pages redirect into the map's sheets.

### Scenario 10: Sign up and land on the map -- happy path

**Goal**: Complete the account-access flow from the sign-in page to the map with the pins sheet open.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/sign-in/ | The sign-in page loads with an email field |
| 2 | Type `tester@example.com` and submit | "Check your inbox" is shown |
| 3 | Open Mailpit at http://localhost:8025 | An email is listed for `tester@example.com` containing an account-access link of the form `http://localhost:8000/account/access/<token>/` |
| 4 | Open the email and click the account link | An "Access your account" confirm page loads (no auto sign-in on the GET); it shows a "Sign in to my account" button (SNOW-439) |
| 5 | Click "Sign in to my account" | Browser POSTs and redirects to the map (`/?panel=favourites`, which the map consumes to `/`) with the pins sheet open; the avatar menu shows Settings and Sign out |

### Scenario 11: Pin a region from the map

**Goal**: Verify the region + date panel's pin control creates a region pin and the same panel lists it.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in (Scenario 10), tap a region on the map | A star roundel sits in the header between the region chip and the download roundel, outlined — the region is not pinned |
| 2 | Click the star roundel | It fills (no page reload). Open the region + date panel from the chip: a row for the region is in the "Pinned regions" section at the foot, marked as the one on screen |
| 3 | Read that row | The region's name and a solid star to unpin — nothing else. No rating, no date, no link: the list is navigation, and the summary above it is what follows the scrubber (SNOW-814) |
| 4 | Open the pins sheet from its roundel | The region is NOT listed there — that sheet lists places (SNOW-814) |
| 5 | Sign out and reload | The star roundel is still there, muted, and links to the sign-in page. The chip is inert with nothing selected, and no "Pinned regions" section renders |

### Scenario 12: Switch region from the pinned list, and unpin

**Goal**: Verify the pinned list navigates, and that unpinning keeps the panel's two controls in agreement.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in with two pinned regions (Scenario 11 twice), deselect every region and press the "No region selected" chip | The chip is pressable and opens onto "Pinned regions" alone |
| 2 | Press one region's name in the list | The map frames that region, the chip renames to it, the URL hash becomes `#<region_id>`, and the panel repaints with that region's summary above the list |
| 3 | Scrub to another date, then read the list again | That region's row is marked as the current one and the others are not, and no row shows a date — only the summary above changes with the scrubber |
| 4 | Press the solid star on that row | The row disappears without a page reload, AND the star roundel in the header hollows — the two read the same list and must never disagree |

### Scenario 13: Delete the account from settings

**Goal**: Verify the "Delete account" control hard-deletes the account.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/settings/ and locate "Delete account" | The control is visible in its own group |
| 2 | Click it | A browser confirmation dialog appears |
| 3 | Click "OK" | Browser is redirected to http://localhost:8000/account/deleted/, which says the account and its data have been deleted; the account is hard-deleted and the session is cleared; the pins sheet on the map is anonymous again |

### Scenario 14: Removing the last pin keeps the account

**Goal**: Verify that unpinning the only region removes the pin and nothing else.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Pin exactly one region (Scenario 11) and open the region + date panel | One row is listed under "Pinned regions" |
| 2 | Press its star | The row goes; the section reads "Pin a region and it will be listed here."; the header roundel hollows; the user stays signed in |
| 3 | Navigate to http://localhost:8000/account/settings/ | The settings page renders — the account survives |

### Scenario 15: The old account URLs redirect into the map

**Goal**: Verify every retired account list page lands on the map with the matching sheet open.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/ | 301 to `/?panel=favourites`; the map opens with the pins sheet open |
| 2 | Navigate to http://localhost:8000/account/routes/ | 301 to `/?panel=routes`; the routes sheet opens |
| 3 | Navigate to http://localhost:8000/account/observations/ and http://localhost:8000/observations/ | Both 301 to `/?panel=reports`; the reports sheet opens |
| 4 | Navigate to http://localhost:8000/favourites/<uuid>/ for one of your pins | 301 to that pin's `/weather/<short_id>/` page |

### Scenario 16: The retired unsubscribe URLs are gone

**Goal**: Verify SNOW-875 left no reachable remnant of the email-subscription
flow. Nothing mints an unsubscribe token any more, so nothing redeems one.
The number is kept rather than reused so the later scenarios — which e2e
docstrings cite by number — do not shift.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In a private window, open `http://localhost:8000/account/unsubscribe/anything/` | 404 |
| 2 | Open `http://localhost:8000/account/unsubscribe-done/` | 404 |
| 3 | Open `http://localhost:8000/account/deleted/` | The account-deleted page renders, with a "Back to Snowdesk" button |

### Scenario 17: Submit the registration form with an invalid email address

**Goal**: Verify validation on the registration form.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In a fresh private window open http://localhost:8000/account/register/ | The registration form loads |
| 2 | Type `notanemail` into the email field and submit | Submission is blocked: either the browser's built-in email validation fires, or the form re-renders in place with a validation error; the page does NOT navigate |

### Scenario 18: Use an expired or invalid account link

**Goal**: Verify the link-expired error page is shown for bad tokens.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/access/expired.invalid.token/ | Page shows "This link has expired" (HTTP 400) with the text "Account links are only valid for 24 hours. This one has expired or is invalid." |
| 2 | Verify the recovery link | A "Request a new link" button is visible |
| 3 | Click "Request a new link" | Browser navigates to http://localhost:8000/account/sign-in/ |

### Scenario 19: Access the account URL with no token

**Goal**: Verify the account endpoint requires a token in the URL path.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/access/ | Browser shows a 404 Not Found page (the URL pattern requires a token segment) |

### Scenario 20: Access the settings page without authentication

**Goal**: Verify unauthenticated users are redirected to the sign-in page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Open a new private/incognito browser window | Fresh session with no cookies |
| 2 | Navigate to http://localhost:8000/account/settings/ | Browser redirects to http://localhost:8000/account/sign-in/ |
| 3 | Navigate to http://localhost:8000/account/manage/ | Browser 301s to the map (`/?panel=favourites`); the pins sheet shows its signed-out state |

### Scenario 21: Returning user re-authenticates via the sign-in page

**Goal**: Verify a returning user sees their existing pins after re-authenticating.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Complete Scenarios 10 and 11 (sign up and pin at least one region, e.g. CH-4115) | The region pin is saved |
| 2 | Open a new private/incognito window (to clear the session) | Fresh session |
| 3 | Navigate to http://localhost:8000/account/sign-in/ | Page loads with a "Sign in" heading, the text "Enter your email address and we'll send you a sign-in link.", and (where WebAuthn is available) a "Sign in with a passkey" button |
| 4 | Enter `tester@example.com` and click "Send sign-in link" | A "Check your inbox" page loads (the same response is shown whether or not the email is registered) |
| 5 | Open Mailpit, find the new email, and click the account link, then the confirm button | Browser lands on the map with the pins sheet open |
| 6 | Verify existing pins | The previously pinned region row (e.g. CH-4115) is listed with its trash control |

### Scenario 22: Sign out via the nav account menu

**Goal**: Verify the authenticated account menu offers sign-out.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in, click the circular avatar button (first letter of your email) in the top nav | A dropdown menu opens with the offline-mode switch, a "Settings" link and a "Sign out" button — no region links |
| 2 | Click "Sign out" | The session is cleared and the browser is redirected to the sign-in page; navigating to http://localhost:8000/account/settings/ now redirects to http://localhost:8000/account/sign-in/ |

### Scenario 23: Deprecated /random/ URL redirects

**Goal**: Verify the old random URL still works via redirect.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/random/ | Browser permanently redirects (301) to http://localhost:8000/examples/random/ |
| 2 | Verify a bulletin loads | A random bulletin renders inline at the `/examples/random/` URL with a region name, date, and danger level |

---

## PWA Shell

End-to-end pass for the service worker + install prompt + update banner
+ offline UI. Runs against the local dev server on `http://localhost:8000`
(service workers are allowed on `localhost` without HTTPS). The related
architecture reference is [`offline-map.md`](offline-map.md); the
compliance index is [`offline-first.md`](offline-first.md).

> **Universal preconditions**
>
> Before every scenario, reset the browser state so results are
> deterministic:
>
> 1. DevTools → **Application → Storage → Clear site data** (ticks
>    "Service Workers", "Cookies and other site data", "Cache storage").
>    This unregisters any prior SW and empties Cache Storage +
>    IndexedDB.
> 2. Close and reopen the tab (or hard-reload with cache disabled) so
>    the next navigation starts from a clean slate.
>
> DevTools panels used throughout: **Application → Service workers**
> (install / waiting / activated state, `Update` and `Unregister`
> links), **Application → Cache storage** (inspect the `snowdesk-shell-*`
> cache), **Network → Offline** throttle (simulate no connection),
> **Application → Manifest**.

### Scenario P1: First visit installs and controls the second load

> Manual-only since SNOW-649 retired the Playwright lifecycle suite. The
> worker's own strategies are unit-tested in
> [tests/js/test_sw.js](../tests/js/test_sw.js) and the `/sw.js` response in
> [tests/public/test_offline_api.py](../tests/public/test_offline_api.py);
> register-and-cache as a journey is this scenario.

**Goal**: Verify the SW registers on first visit, caches the shell, and
serves the second load from cache.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Reset state, then navigate to http://localhost:8000/ | Page loads normally over the network |
| 2 | Open DevTools → Application → Service workers | An `activated and is running` entry appears for `http://localhost:8000/` with source `sw.js`; no waiting worker |
| 3 | Open Application → Cache storage → `snowdesk-shell-vN` | Entries include `http://localhost:8000/`, `/static/offline.html`, and the hashed CSS/JS bundles referenced by the page |
| 4 | Reload the page (Cmd+R / F5, with "Disable cache" **off**) | Network tab shows the shell entries served from `(ServiceWorker)`; Console has no SW errors |

### Scenario P2: Install prompt (Chromium desktop / Android)

> Manual-only: Chromium's install engagement heuristic (scroll/tap
> thresholds before `beforeinstallprompt` fires) is not drivable from
> Playwright. The install funnel's telemetry (`pwa.install.prompted` /
> `.accepted` / `.dismissed` / `.completed`) IS covered, along with the
> eligibility gate and the 30-day cool-off, in
> [tests/js/test_pwa_install.js](../tests/js/test_pwa_install.js).

**Goal**: Verify `#pwa-install-banner` reveals when the browser fires
`beforeinstallprompt`, and that clicking Install completes the flow.

**Preconditions**: Chrome / Edge / any Chromium browser. The install
prompt only fires when the site is not already installed — if the app
is installed, uninstall it first (Chrome ⋮ menu → "Uninstall Snowdesk"
from within the standalone window).

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Reset state, navigate to http://localhost:8000/ and interact with the map (scroll, tap a region) until Chrome's install engagement threshold fires | The `#pwa-install-banner` slides in over the bottom-right of the viewport with a "Snowdesk" title, one-line body, and an "Install" primary CTA + `×` dismiss |
| 2 | Click "Install" | The browser's native install dialog opens with the manifest name, icon, and screenshots (Application → Manifest lists the same); accepting installs the app and closes the tab / opens a standalone window |
| 3 | Reopen the tab and reset state; interact again to re-fire the prompt; this time click `×` | Banner disappears and does not re-appear during this session; a `pwa.install.dismissed` event is emitted (see Application → IndexedDB → `snowdesk-pwa` → `queue:events` if telemetry is on) |

### Scenario P3: iOS install guide

> Manual-only: requires real Safari-on-iOS chrome (Share sheet, Add to
> Home Screen), which Chromium/Playwright cannot exercise.

**Goal**: Verify Safari on iOS shows the static "Share → Add to Home
Screen" hint (iOS has no `beforeinstallprompt`).

**Preconditions**: iOS Safari on a real device or a Simulator, browsing
to `http://<your-lan-ip>:8000/` (the dev server must be reachable on
the LAN — `runserver 0.0.0.0:8000`).

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Reset state, load the map page, and interact with it | `#pwa-install-ios` appears with an animated arrow pointing to the Share icon in the Safari toolbar and the message "Tap Share, then Add to Home Screen" |
| 2 | Follow the guide: Share → Add to Home Screen → Add | The Snowdesk icon appears on the home screen using the Apple touch icon (not a screenshot of the page); tapping it opens the app in standalone mode without Safari chrome |

### Scenario P4: Update banner via a new sw.js (SW-driven path)

> Automated in part: what the banner shows, and what Reload does when it is
> pressed, are covered in
> [tests/js/test_sw_register_update_feedback.js](../tests/js/test_sw_register_update_feedback.js)
> and [tests/js/test_sw_register_update_throttle.js](../tests/js/test_sw_register_update_throttle.js).
> The browser-level byte-diff test went with the Playwright lifecycle suite in
> SNOW-649, so the trigger itself is manual — and Playwright could not observe
> or intercept a service worker's own script fetch in any case, which the
> SNOW-389 spike established before its artefact was removed with the suite.

**Goal**: Verify the soft update banner appears when a new SW installs,
and clicking Reload lands cleanly on the new shell in a single reload.

**Preconditions**: Complete Scenario P1 first so an SW is already
controlling the page. Touch any shell source (e.g. add a comment to
[static/js/sw.js](../static/js/sw.js)) — the derived `CACHE_VERSION` changes with it (SNOW-590)
(e.g. `'snowdesk-shell-v8'` → `'snowdesk-shell-v99-test'`) — this is
the change that causes the browser to detect a new SW. Revert after
the scenario.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | With the tab open, DevTools → Application → Service workers → click **Update** (top right) | A second SW appears in state `installed, waiting to activate` alongside the still-`activated` prior worker |
| 2 | Look at the page | The `#sw-update-banner` slides in at the bottom-centre: refresh icon, "Update available", "A newer version of Snowdesk is ready.", and a "Reload" primary CTA + `×`. Where `/api/version` is reachable and the builds can be told apart, the copy names them instead — "Update available (v31)" / "You are on v30. Reload to update to v31." Neither state says anything about what the reload keeps (SNOW-869) |
| 3 | Click "Reload" on the banner | The banner button briefly disables; the waiting worker activates (Service workers panel: the new SW becomes `activated and is running`, the old one disappears); the page reloads exactly once onto the new shell (URL and content preserved) |
| 4 | Reload one more time | No banner appears — you are already on the latest version |

### Scenario P5: Update banner via server X-App-Version drift (header path)

> Automated: [tests/js/test_pwa_version_check.js](../tests/js/test_pwa_version_check.js)
> — the drift-is-only-a-hint rule, the authoritative `/api/version` round
> trip, the soft banner on a confirmed drift, and the shell wipe that spares
> the pinned basemap buckets. The endpoint's own side is
> [tests/public/test_pwa_version_api.py](../tests/public/test_pwa_version_api.py).
> The browser journey went with the Playwright lifecycle suite in SNOW-649.

**Goal**: Verify the same banner also appears when `sw.js` is unchanged
but the server has moved on, and that Reload clears the shell caches so
the reload picks up the fresh HTML (no reload loop).

**Preconditions**: Scenario P1 completed; the dev server running.
Restart the server with an overridden version so the response header
`X-App-Version` differs from the `<meta name="pwa-app-version">` that
was baked into the currently-loaded page:

```bash
APP_VERSION=test-newer-build uv run python manage.py runserver
```

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Keep the tab open, then trigger any request (scroll the timeline, tap a region — anything that fires a `fetch` or HTMX call) | `pwa_version_check.js` sees the header mismatch, confirms it against the `/api/version` body (Network panel: one `no-store` request to `/api/version`), and reveals `#sw-update-banner` |
| 2 | DevTools → Application → Service workers | Only one SW is registered — no waiting worker (the SW itself did not change) |
| 3 | Click "Reload" on the banner | Application → Cache storage: the `snowdesk-shell-*` entries are cleared before the reload; the page reloads once and the new shell's `<meta name="pwa-app-version">` now matches the header — the banner does not re-appear |

A header mismatch the `/api/version` body does **not** back — e.g. a
response replayed from the browser HTTP cache right after a deploy —
reveals nothing (automated: the "does not re-verify a header the server has
already disowned" and "cannot confirm" cases in
[tests/js/test_pwa_version_check.js](../tests/js/test_pwa_version_check.js)).
This is the fix for the staging stuck-banner bug, where Reload could
never clear a banner triggered by stale cached headers.

### Scenario P6: Forced update via APP_BLOCKED_VERSIONS

> Automated: [tests/js/test_pwa_version_check.js](../tests/js/test_pwa_version_check.js)
> — the modal waits for the click, touches nothing until it, emits
> `pwa.forced_update.triggered` once, and spares the pinned buckets. The
> server-side verdict is
> [tests/public/test_pwa_version_api.py](../tests/public/test_pwa_version_api.py)
> (`test_update_required_true_for_a_blocked_client` and its fail-open cases).

**Goal**: Verify the blocking modal appears when the server names this
build as blocked, that it *waits* for the click, and that the click
refreshes the app without destroying downloaded data (SNOW-609).

**Preconditions**: Scenario P1 completed, plus one downloaded region
(Scenario P9's "Download area" flow) so there is something to lose.

`APP_BLOCKED_VERSIONS` is a comma-separated set of build identifiers,
matched against the `X-Client-Version` the page sends on every
same-origin request — which is the `APP_VERSION` the shell was delivered
on, i.e. `dev` locally. Since the check needs an `X-App-Version` drift
to schedule its `/api/version` round trip, restart with a *different*
current build than the loaded tab was served:

```bash
# Terminal: the tab is already open on a shell served with APP_VERSION=dev
APP_VERSION=test-newer-build APP_BLOCKED_VERSIONS=dev uv run python manage.py runserver
```

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the still-open tab, trigger any request (scroll the timeline, tap a region) | DevTools → Network: one `no-store` request to `/api/version` whose body reads `"update_required": true`; `#pwa-update-modal` opens as a full-viewport overlay reading "Update required" / "This version of Snowdesk is no longer supported. Reload to continue.", and a single "Reload now" CTA; no dismiss control; underlying page scroll is locked. The copy says nothing about local state — the click clears shell caches only, so there is nothing to disclose (SNOW-869) |
| 2 | Wait 30 seconds without touching anything | The modal is still open and the page has not reloaded. Application → Cache storage still shows every bucket, including `snowdesk-basemap-*` |
| 3 | Click "Reload now" | The `snowdesk-shell-*` / `map-shell-*` buckets are cleared and the page reloads once. The `snowdesk-basemap-*` buckets, IndexedDB (`snowdesk-pwa-v1`) and `localStorage` are all still there — the downloaded region is still available offline |
| 4 | Restart the server without `APP_BLOCKED_VERSIONS` and reload | `/api/version` reads `"update_required": false`; no modal; the app operates normally |

A client that sends no `X-Client-Version` at all is never blocked, by
design — there is no recovery path from a blocking modal on a build the
server cannot identify.

### Scenario P7: Offline reload of a cached page (incl. /?d=YYYY-MM-DD)

> Automated: [test_pwa_lifecycle_offline.py::test_offline_reload_of_visited_date_url](../tests/e2e/test_pwa_lifecycle_offline.py)
> and [::test_offline_reload_of_never_visited_date_url](../tests/e2e/test_pwa_lifecycle_offline.py)
> (the SNOW-347 regression guard) — the `?d=` URL is moved client-side
> via `history.replaceState`, the same mechanism `map.js`'s `commitDate()`
> uses when scrubbing (MapLibre tiles don't load in headless Chromium, so
> driving the actual scrubber UI isn't reliable here — see
> [tests/js/test_scrubber_core.js](../tests/js/test_scrubber_core.js) and
> [tests/js/test_map_scrubber_reveal.js](../tests/js/test_map_scrubber_reveal.js)
> for the scrubber's own coverage).

**Goal**: Verify a page that was successfully loaded online serves from
cache when offline, including `/?d=X` variants that only exist via
`history.replaceState` (fixed in [PR #347](https://github.com/hugorodgerbrown/snowdesk-data-pipeline/pull/347)).

**Preconditions**: Scenario P1 completed. The map page loaded at least
once online since `Clear site data`.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Load http://localhost:8000/ online; scrub the timeline so the URL becomes `/?d=2026-02-17` (any date the fixture covers) | Map renders and paints the choropleth for the selected date |
| 2 | DevTools → Network → set throttling to **Offline** | The header's connectivity symbol (`[data-network-indicator]`) switches to the struck-through mark and `data-network-state="offline"`; nothing else moves on the page (see P8) |
| 3 | Reload the page (Cmd+R) | The map shell renders from cache; the URL is preserved at `/?d=2026-02-17`; the choropleth paints from `/api/ratings/` (which is stale-while-revalidate cached — see the STATIC_PATHS list in `sw.js`); no "You're offline" page appears |
| 4 | Also try reloading with a `?d=` value you did **not** visit online (e.g. `/?d=2026-03-01`) | Same behaviour — the `ignoreSearch: true` cache-match fallback in `_networkFirst` finds the cached `/` shell; the JS reinitialises to 1 March |

### Scenario P8: Connectivity symbol + freshness toast + network-required controls

> Automated: [tests/js/test_pwa_offline.js](../tests/js/test_pwa_offline.js)
> — the symbol's two appearances, the panel's four states, the switch,
> and the `data-network-required` gating are all jsdom-observable, so
> they are covered in Vitest rather than in a browser (SNOW-649 removed
> the e2e test that used to sit here). What remains manual is the visual
> one: that the panel is legible and stays inside the viewport.
>
> Correction from implementation: `data-network-required` sits on the
> `<form>` element (`aria-disabled` lands there, not on the email
> `<input>` itself); `pwa_offline.js`'s `syncNetworkRequired()` also
> directly `disabled`s any `<button>` descendant, which is what actually
> stops the Subscribe click.

**Goal**: Verify the permanent connectivity symbol (SNOW-377 / SNOW-748)
tracks the connection state, that the panel behind it surfaces the last
sync timestamp and explains the state, and that any form or button
carrying `data-network-required` is disabled.

SNOW-748 removed the `#pwa-offline-banner` strip this scenario used to
describe. The symbol in the header is now permanent — it is on screen in
every state rather than only in the failure case — and the freshness
stamp and the explanation moved into `#pwa-connection-panel`, one press
away, anchored under the symbol rather than fixed to the bottom of the
viewport.

**Preconditions**: Scenario P1 completed. A bulletin URL such as
http://localhost:8000/ch-4115/martigny-verbier/2026-04-08/ visited
online at least once so the timestamp is primed.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Load the bulletin page online; press the connectivity symbol in the header | Symbol shows the plain arcs and `data-network-state="online"`; the panel opens directly beneath it, on the card surface (not a status colour), reading "Online — last synced <relative>" with the "using the network" explanation and no reconnect button. Press again to close, or press Escape, or click the map behind it |
| 2 | DevTools → Network → **Offline**, then trigger any request | The symbol switches to the struck-through mark; NO panel appears on its own. Press it: the panel reads "Offline — last synced <relative>" with the "lost contact" explanation |
| 3 | Scroll to the bulletin's "Get avalanche alerts" subscribe form | The Subscribe button is disabled (grey / no-hover); the enclosing `<form>` carries `aria-disabled="true"` and `pointer-events: none`, so the email input is unreachable too. This is `data-network-required` in action |
| 4 | Network → **No throttling** (back online), and trigger any request | The symbol returns to the plain arcs; an open panel repaints to the online copy live; subscribe form re-enables; no page reload needed |
| 5 | In the same menu, switch **Offline mode** on | The switch is the first control under the rule, below the explanations; the symbol goes struck-through while `navigator.onLine` is still true, and the menu now reads "Offline mode — last synced …" with "You asked the app to stay offline" and a **Use the network again** button |
| 6 | Sign out entirely and repeat step 5 | The switch is still there (SNOW-921 — it is a device preference, not an account feature), and still works. This is the step that would have been impossible before: a signed-out reader could only escape a mode the worker chose for them, never choose one |
| 7 | Sign out and reach step 5's state via the worker's own latch (three failed reads) | The menu offers **Try reconnecting** as well as the switch; both return to `auto` |
| 8 | With the menu open, press its "×" (top-right), then reopen it and press Escape | Each closes the menu and returns focus to the symbol; the "×" is a full 44×44 target, not a hairline glyph |
| 9 | Watch the two small arrows beside the wifi mark while the page loads and while panning the map | The up arrow lights as requests go out and the down arrow as responses land, each for about half a second; a burst holds them lit. Leave the tab idle for a minute: they stay dark — the telemetry flush is excluded on purpose |
| 10 | As a GRP_DEBUG member, open the menu and press **Debug log** | The menu closes and the on-device trace panel opens bottom-left. Pressing it again with the trace already open leaves it open — the row opens, it does not toggle |

### Scenario P9: Offline navigation to a URL never visited

> Automated: [test_pwa_lifecycle_offline.py::test_offline_navigation_to_never_visited_url_shows_offline_fallback](../tests/e2e/test_pwa_lifecycle_offline.py)

**Goal**: Verify the branded `/static/offline.html` fallback surfaces
when both the network and the cache miss.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Reset state, load `/` online once so the SW is controlling | SW `activated`; only `/` is in the navigation cache |
| 2 | DevTools → Network → **Offline** | The connectivity symbol goes struck-through |
| 3 | Navigate to http://localhost:8000/some-page-never-visited/ (address bar) | The branded "You're offline" page renders — Snowdesk wordmark, "Snowdesk needs a network connection to fetch the latest bulletin.", and a "Retry" button. No stack trace, no Chrome error page |
| 4 | Network → back online, click "Retry" | Navigation proceeds normally |

### Scenario P10: Kill switch A — /api/sw-config flip

> Automated in part: the endpoint's states are covered server-side in
> [tests/public/test_pwa_version_api.py](../tests/public/test_pwa_version_api.py)
> (`test_sw_config_default_shape`, `test_sw_config_kill_true_evicts_client`,
> `test_sw_config_can_swap_sw_url`). The browser half — a fresh tab
> unregistering rather than registering — went with the Playwright lifecycle
> suite in SNOW-649 and is the walkthrough below.

**Goal**: Verify setting `SW_KILL=true` causes new tabs to unregister
their SW without ever registering a new one (Mechanism A —
pre-register gate in `sw_register.js`).

**Preconditions**: Scenario P1 completed so an SW is currently
registered. Restart the server with:

```bash
SW_KILL=true uv run python manage.py runserver
```

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | `curl -s http://localhost:8000/api/sw-config` | Returns JSON `{"sw_url": "/sw.js", "kill": true}` |
| 2 | Open a new tab to http://localhost:8000/ (do not reuse a tab that has a controller from before the flip) | Page loads normally without a SW in control; DevTools → Application → Service workers reports no registration for this origin |
| 3 | Reload — every subsequent navigation is a plain fetch | No `snowdesk-shell-*` cache entries are populated; no offline behaviour applies |
| 4 | Restart the server without `SW_KILL`, reset state, reload | SW registers again as in Scenario P1 |

### Scenario P11: Kill switch B — swap sw.js for sw-kill.js

> Manual-only: a `test_kill_switch_b_wipes_and_unregisters` test was
> written and initially looked solid, but a wider SNOW-389 anti-flake
> pass surfaced a genuine, non-marginal "did not converge to zero
> registrations" failure in the install → skipWaiting → activate → wipe
> → unregister chain — raising the poll deadline did not fix it. Dropped
> per the scope's fallback ladder ("flaky > absent, but flaky < manual").
> The spike's `_spike_results.py` artefact was removed with the Playwright
> lifecycle suite in SNOW-649; its findings are the two paragraphs here.
>
> Correction from that implementation attempt, still useful for the
> manual walkthrough below: `registration.update()` (DevTools' "Update"
> button) only re-fetches the CURRENTLY registered script URL; it cannot
> pick up a changed `sw_url`. What actually re-reads `/api/sw-config` is
> `sw_register.js`'s top-level `fetchSwConfig()`, which runs on every
> fresh page load — step 1 below needs a reload, not an in-place DevTools
> update, to trigger Mechanism B.

**Goal**: Verify pointing `SW_URL` at `/sw-kill.js` swaps every
already-installed client onto the wipe-and-unregister worker
(Mechanism B — for clients that already have a controller and won't
run Mechanism A until next launch).

**Preconditions**: Scenario P1 completed. Restart with:

```bash
SW_URL=/sw-kill.js uv run python manage.py runserver
```

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the tab still open from P1, reload the page (not DevTools' "Update" button — see the correction above) | A new worker (source `sw-kill.js`) installs and activates |
| 2 | Observe the SW panel and Cache storage | The kill worker unregisters itself once it has cleared all caches; Cache storage becomes empty; the registration list becomes empty within a few seconds |
| 3 | Reload the tab | Page loads over the network; no SW controls it; `sw_register.js` re-registers `/sw.js` if `SW_URL` was reverted, or `/sw-kill.js` again if not |
| 4 | Restart the server with defaults (`SW_URL=/sw.js`, `SW_KILL=false`), reset state, reload | Back to Scenario P1's clean state |

### Scenario P12: Reset local data (settings page button)

> Automated: [tests/accounts/test_pwa_reset.py](../tests/accounts/test_pwa_reset.py)
> asserts the settings page carries the trigger and its copy;
> [tests/js/test_pwa_reset.js](../tests/js/test_pwa_reset.js) covers the
> clearing itself, including the `onblocked` IndexedDB delete that must not
> count as a success. The browser journey went with the Playwright lifecycle
> suite in SNOW-649.
>
> Two corrections from implementation. The control moved from
> `/account/manage/` to `/account/settings/` in SNOW-667 — `/account/manage/`
> is now a 301 to `/?panel=favourites` and has no button on it. And
> `[data-pwa-reset-trigger]` is bound by `pwa_reset.js`, which gates on a
> native `window.confirm()` dialog, not the `#pwa-reset-required` overlay:
> that overlay is a distinct, unrelated mechanism — `db.js`'s terminal Reset
> Required state after an IndexedDB migration failure — and is never shown by
> this button.

**Goal**: Verify the "Reset local data" control on the manage page
clears IndexedDB + Cache storage + unregisters the SW.

**Preconditions**: Signed in (Scenario 10 or 21). Scenario P1
completed so state exists to clear.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/settings/ and find the "Reset local data" row | The row's heading names what is reset and a short line explains it; the button beside it reads **Reset** (SNOW-746 moved the label onto the heading) |
| 2 | Click "Reset" | A native confirm dialog opens, summarising what will and won't be cleared |
| 3 | Accept the dialog | **Every** Cache Storage bucket goes — the shell and the pinned `snowdesk-basemap-*` downloads alike, since `pwa_reset.js` walks `caches.keys()` — along with every IndexedDB database and local/sessionStorage; the SW is unregistered and the page reloads onto a fresh one. This is not a downloads-only control: to clear one area, use the bin on its row in the downloads sheet (D7) |

### Scenario P12b: Reset local data (offline fallback page)

> Automated: [tests/js/test_offline_page_reset.js](../tests/js/test_offline_page_reset.js)
> asserts the control against the shipped `static/offline.html` rather than a
> fixture copy, so the page and `pwa_reset.js` cannot drift; the server-side
> half — that the page loads only that one script and carries the trigger —
> is in [tests/public/test_offline_api.py](../tests/public/test_offline_api.py).
> The browser journey went with the Playwright lifecycle suite in SNOW-649.
> See [`offline-first.md`](offline-first.md#reset-local-data-snow-378).

**Goal**: Verify the same "Reset local data" control on
`static/offline.html` — the surface that reaches a user who is stuck
*and* offline. The manage page carries its own copy, but that page is
cached per account (SNOW-607), so its copy is present only once that
account has loaded it online in this browser. The offline page is
pre-cached and carries no account identity, so it is always there.

**Preconditions**: Scenario P1 completed (a SW is registered and the
shell is cached), so there is state to clear.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/static/offline.html | The branded fallback page renders; below Retry, a "Reset local data on this device" control with a short explanation |
| 2 | Click "Reset local data on this device" | The same native confirm dialog as P12 opens |
| 3 | Accept the dialog | IndexedDB, Cache storage and Service workers are cleared; the page reloads |
| 4 | Go offline (DevTools → Network → Offline), navigate to a URL never visited (e.g. http://localhost:8000/some-page-never-visited/) | The fallback page renders **with** the reset control visible and working — `/static/js/pwa_reset.js` is in `PRECACHE_URLS` (`static/js/sw.js`) alongside the page itself, so it loads with no network. The panel reveals itself only once that script has defined `window.pwaResetLocalData`, so a control bound to nothing is never shown |

### Scenario P13: A downloaded area that cannot render, and the repair

> Not automated in a browser, deliberately. Every assertion here is
> reachable from jsdom and lives in Vitest —
> [tests/js/test_map_multi_source_basemap.js](../tests/js/test_map_multi_source_basemap.js)
> (the probe, the roundel's `incomplete` state and the repair loop),
> [tests/js/test_map_downloads_manager.js](../tests/js/test_map_downloads_manager.js)
> (the sheet's row and its Repair control) and
> [tests/js/test_basemap_download_runner.js](../tests/js/test_basemap_download_runner.js)
> (the repair never reaches the eviction sequence). A browser test could
> not run this journey anyway: a download's fetches are made by the
> service worker, which Playwright's route interception does not see (see
> `tests/e2e/conftest.py`), so the run would have to reach a real tile
> origin from CI — and with the basemap style unreachable there, MapLibre
> never fires `load` and the roundel never resolves a tile source at all.

**Goal**: Verify that an area whose bucket holds every tile but not the
documents MapLibre needs to draw them says so, and that one tap fixes it
(SNOW-844).

A pinned area renders offline only if its bucket also holds the basemap's
style document, the TileJSON each vector source is declared by, and the
sprite. An area downloaded before SNOW-843 never fetched its TileJSON at
all, so this is reproducible on a real device that has been using the app
for a while — not only by hand-editing a bucket.

**Preconditions**: one downloaded region, on the basemap currently
showing. Swisstopo is the sharpest case: its style declares two vector
sources, each by a TileJSON document.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Download a region, wait for the roundel to go solid | The roundel reads `done`; DevTools → Application → Cache storage → `snowdesk-basemap-pinned-region-<id>` holds the tiles, the style JSON, both `tiles.json` documents and the sprite |
| 2 | Delete ONE of the `tiles.json` entries from that bucket, then reload | The roundel reads `incomplete` — amber, with the exclamation glyph — and its tooltip says the basemap is missing part of itself. It is NOT `done` (the tiles are all still there, which is the whole bug) and NOT `idle` |
| 3 | Open "Manage downloads" | The area's row reads "Incomplete", dims like an orphan, and carries a **Repair** control the other rows do not |
| 4 | Tap the roundel | It goes busy briefly, then back to `done`. The Network panel shows ONE request — the deleted `tiles.json` — and no tiles: a repair fetches what is missing, not the area again |
| 5 | Repeat step 2, then go offline before tapping | The roundel reads `offline`, not `incomplete` — a repair is a fetch, and the same refusal applies. Back online, `incomplete` returns |
| 6 | Switch to a basemap this area was NOT downloaded under, and open the sheet | Its row is NOT marked incomplete. A record written before SNOW-844 names no dependencies, and a style that is not loaded cannot be asked what its sprite is, so an unanswerable row is left alone rather than accused |

---

## Offline Downloads (map coverage)

One pass over the downloads feature, weighted towards the question it
exists to answer: **where does the stored map stop?** Architecture
reference: [`offline-map.md`](offline-map.md) ("Download basemap",
"Custom-area download", "Download budget and whole-area eviction",
"Downloaded-tiles overlay", "Manage downloads" sheet, "Account sync for
download areas"). The user-facing copy is the "Offline downloads" panel
on `/help/`.

> **What a download is, before you judge any result**
>
> - It stores **basemap tiles only**. Bulletin pages and danger colours
>   cache separately, by visiting them (see PWA Shell above).
> - The band is **z10–14** (`MICRO_BAND`). Past z14 the stored tiles
>   overzoom (bigger, no new detail).
> - Below z10 a **shared base layer** takes over (SNOW-856) — z0–7 over
>   the whole area the camera can reach, fetched on the tail of any
>   download and shared by every area under that basemap. So zooming out
>   offline keeps drawing. z8 and z9 are deliberately not stored
>   (SNOW-863: they were 86% of the layer's size); they draw from the z7
>   tile, softer but never blank.
> - **Consequently the map no longer goes blank outside a download.**
>   MapLibre stretches a stored z9 tile over ground you never fetched, so
>   at z11 one valley over you get a coarse basemap rather than nothing.
>   That is deliberate (accepted 2026-09-07).
> - **So the coverage overlay is what marks the edge, and offline it turns
>   itself on** (SNOW-857) unless you have set the "Display downloaded
>   areas" row yourself — a hatch over ground downloaded for the basemap you
>   are on, an outline in another basemap's colour over ground downloaded
>   for that one. The legend card carries the key while it is showing.
> - A **region** download is clipped to the region's real boundary plus
>   about one z14 tile (~1.7 km) of margin — not its bounding rectangle.
>   A **custom area** genuinely is the rectangle you framed.
> - One Cache Storage bucket per area:
>   `snowdesk-basemap-pinned-region-<REGION_ID>` or
>   `snowdesk-basemap-pinned-custom-<uuid>`, plus one
>   `snowdesk-basemap-pinned-base-<basemapKey>` for the shared layer.
> - Coverage is **per basemap**. An area stored under OpenFreeMap is not
>   coverage for swisstopo.
> - Tiles alone are not coverage. A bucket also needs its **render
>   dependencies** — the style document, the TileJSON each vector source
>   is declared by, and the sprite (SNOW-844). An area holding every tile
>   and missing one of those reads `incomplete`, not `done`, and is
>   repaired rather than re-downloaded: that is P13's subject, and these
>   scenarios assume it passes rather than repeating it.
> - Per-run ceiling 200 MB; standing budget 500 MB, device-local and
>   settable in the sheet.

> **Preconditions**
>
> 1. The prerequisites at the top of this file (server, Tailwind, seeded
>    data — the `eaws_*` fixtures already carry each region's precomputed
>    `basemap_download`, so no extra command is needed).
> 2. **Signed in** (Scenario 10 or 21) — *starting* a download needs an
>    account. D9 covers the signed-out and offline gates.
> 3. Browser state reset per the PWA Shell universal preconditions above.
> 4. DevTools: **Application → Cache storage**, **Network → Offline**,
>    **Console**.

**Coverage probe.** Paste into the Console at any point to see exactly
what is on disk, per area and per zoom:

```js
for (const n of (await caches.keys()).filter(n => n.startsWith('snowdesk-basemap-pinned-'))) {
  const urls = (await (await caches.open(n)).keys()).map(r => r.url);
  const byZoom = {};
  for (const u of urls) {
    const m = u.match(/\/(\d{1,2})\/\d+\/\d+/);
    if (m) byZoom[m[1]] = (byZoom[m[1]] || 0) + 1;
  }
  console.log(n, urls.length, 'entries', byZoom,
    'glyphs:', urls.filter(u => /font/i.test(u)).length,
    'docs:', urls.filter(u => /\.json/i.test(u)).length);
}
```

Healthy region bucket: tiles at **10, 11, 12, 13, 14 and no other zoom**,
a non-zero `glyphs` count (labels survive offline) and a `docs` count
covering the style, one TileJSON per vector source, and the sprite —
without those the pinned tiles are unreachable however complete they are.
A `docs` count that looks short is the `incomplete` state's territory:
run P13 rather than diagnosing it from here.

### Scenario D1: Download a region

**Goal**: The per-region roundel runs a download and reports the cache
truthfully across a reload.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Select CH-4115 (Martigny/Verbier) — search or tap the map | The readout chip fills, and a download roundel appears beside the region name |
| 2 | Hover / inspect the roundel | `data-download-state="idle"`; the tooltip carries the size ("up to N MB") |
| 3 | Tap it | State goes `busy`: the roundel fills bottom-up in the active basemap's colour, and the on-map grid fills square by square |
| 4 | Wait for it to finish | State goes `done` — a solid disc, same glyph in white. **No toast**: the roundel is the only feedback. `done` asserts both halves: every tile, *and* every render dependency (P13) |
| 5 | Run the coverage probe | One bucket, `snowdesk-basemap-pinned-region-CH-4115`, with tiles at z10–14 only, plus glyphs and docs |
| 6 | Reload the page, reselect the region | Still `done` — the state is a live cache read of both halves, never a stored flag |

### Scenario D2: See the coverage you hold

**Goal**: The sheet and the on-map squares agree with what is on disk.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Tap the framed-square roundel, bottom-right | The "Your downloads" sheet opens |
| 2 | Read the header | Budget row: segmented bar, "Using N MB of", and a 500 MB pill. Caption: areas follow the account, map data and budget stay on this device |
| 3 | Read the list | One group headed by the active basemap (swatch + name + this device's total); D1's row reads `Region · N MB` |
| 4 | Close the sheet, open the layers menu and turn on "Display downloaded areas" (under Basemap) | Translucent squares appear over the downloaded area, one per z14 tile, in the basemap's identity colour |
| 5 | Close the menu | The squares **stay** — that row is the only thing that turns them off |
| 6 | Compare the shaded edge with the region outline | Coverage follows the boundary plus roughly one tile of margin — a ragged edge, not a rectangle |

### Scenario D3: Where coverage stops, offline

**Goal**: The headline test. Inside the shading the map draws with no
network; outside it, it does not — and neither state is a broken page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | With D1 downloaded and the squares on, note a landmark just inside and one just outside the shaded edge | — |
| 2 | DevTools → Network → **Offline** (or the network menu → Offline mode) | The header network symbol switches to the struck-through glyph |
| 3 | Hard-reload the map page | The map page loads from the shell cache; region overlays and the danger choropleth paint |
| 4 | Pan to the landmark **inside** coverage | Basemap tiles draw, **with place labels** — glyphs are promoted into the pinned bucket at the end of a run |
| 5 | Pan just past the shaded edge | The basemap keeps painting, but **coarsely** — stretched z9 tiles from the shared base layer (SNOW-856), not the detail you downloaded. The hatched overlay is what marks the real edge, and offline it is on by default (SNOW-857). Overlays keep painting either way. No error page, no spinner that never ends |
| 6 | Inside coverage, zoom past z14, then out below z10 | Past z14 the tiles overzoom — larger, no new detail, never blank. Below z10 the shared base layer draws (SNOW-856): less detail, never blank |

### Scenario D4: Download a custom area

**Goal**: Framing behaves under the ceiling and stores the box you framed.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Open the sheet → "Download a custom area" | Map furniture (ribbon, legend, control stack) disappears; a dim mask with a fixed frame and a CTA sheet reading "up to N MB" |
| 2 | Pan and zoom the map under the frame | Zoom pivots on the **frame**, not the pointer; the ground under the frame does not shift through a zoom gesture; the readout tracks with no flicker |
| 3 | Zoom out until the readout reaches 200 MB | The frame locks to a fixed ground size and stops growing; the readout holds at the ceiling |
| 4 | Zoom back in | The frame releases and refills the gutter — the transition is continuous, no jump |
| 5 | Tap Download | Progress runs; on completion the CTA reads "N MB downloaded", Download is hidden, Cancel is relabelled Close |
| 6 | Close, then reopen the sheet | A new row under the same basemap heading (`Custom area · N MB`, renameable via the pencil); the squares extend over the framed box; the probe shows a new `custom-<uuid>` bucket |

### Scenario D5: Coverage is per basemap

**Goal**: A basemap switch is a switch to a map you have not stored, and
the UI says so rather than looking like a deletion.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | With D1/D4 stored, open the layers menu and switch basemap (e.g. OpenFreeMap → Swisstopo winter) | The map repaints in the new basemap |
| 2 | Look at the squares | They repaint for the **new** basemap — i.e. empty. Nothing was deleted |
| 3 | Reselect the downloaded region | The roundel is a **hollow ring** (`other-basemap`) painted in the *other* basemap's colour — not `idle`, not `done`, and still tappable |
| 4 | Open the sheet | The rows sit under the previous basemap's heading; the new basemap has no group of its own |
| 5 | Back online, tap the ring | The same region downloads under the active basemap; afterwards both basemaps read `done` and the bucket's byte total has grown |
| 6 | Go offline and switch basemap back and forth | Each basemap draws only where it was stored |

### Scenario D6: Budget and eviction

**Goal**: Making room removes whole areas, with consent — it never
perforates the areas that remain.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the sheet header, set the budget to the smallest value that still holds one area | The bar re-segments; nothing is deleted |
| 2 | Download further areas until the next run needs room | A confirm banner appears **before** any fetch, naming the exact areas that would go (oldest first) |
| 3 | Dismiss it with × | Nothing is downloaded and nothing is removed; the existing rows and squares are unchanged |
| 4 | Repeat and accept | The named areas disappear from the list *and* their squares disappear whole; the surviving areas keep unbroken coverage (re-run the probe: their z14 counts are unchanged) |
| 5 | Frame an area larger than the whole budget | Refused outright with the budget toast — distinct from the device-quota toast — and no run starts |
| 6 | Lower the budget below what you already hold | The sheet reports being over budget; nothing is deleted behind your back |

### Scenario D7: Remove, and an interrupted run

**Goal**: A deletion is complete, and a half-finished download never
claims to be coverage.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Tap the bin on a row | The row goes, its squares go, and its bucket is gone from the probe |
| 2 | Start a region download and interrupt it mid-run (Network → Offline while `busy`) | The run reports cancelled, not failed; the roundel rests at `idle` — a partial run records nothing |
| 3 | Reopen the sheet | The leftover bucket lists under "Unknown basemap" with its bucket id as the title and "Incomplete" as its whole meta line — no size, no kind, **Remove the only action** |
| 4 | Note what distinguishes it from P13's row | Both read "Incomplete", and the actions are the difference: an orphan has no record to repair *from*, so it gets Remove alone, where a render-incomplete area carries **Repair** as well |
| 5 | Remove it, re-run the probe | The bucket is gone |

### Scenario D8: Areas follow the account, tiles do not

**Goal**: A listed area is not the same thing as an available one.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | With areas downloaded in browser A, sign in as the same account in a clean profile B | — |
| 2 | Open the sheet in B | Each area is listed, dimmed, with **no size**, and carries a "Download here" button |
| 3 | Turn on "Display downloaded areas" in B's layers menu | No squares — B holds no tiles. The budget total in B reads 0 for those rows |
| 4 | Tap "Download here" | A region downloads directly; a custom area reopens framing fitted to the stored box |
| 5 | Rename the custom area in A, then reload B | The new name shows in B — the definition syncs, the bytes never do |

### Scenario D9: The two gates

**Goal**: Signing out and going offline restrict *starting* a download
and nothing else.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Sign out, then select a region and open the sheet | The region roundel and "Download a custom area" are still visible and tappable, and take you to sign-in |
| 2 | While signed out, check what you already hold | The sheet still lists, sizes, renames and deletes; the squares still draw; an offline reload still shows the stored map |
| 3 | Sign back in, then go offline (Network → Offline, or the network menu → Offline mode) | The sheet's add-CTA is disabled and reads "Downloading needs a connection"; the region roundel is dimmed and non-actionable (`aria-disabled="true"`) — both explained, neither hidden |
| 4 | Return online | Both controls re-enable without a reload |

### Scenario D10: The full offline run, end to end

**Goal**: The pass to run before shipping anything that touches the
worker, the download runner or the basemap layers. D1–D9 each isolate one
mechanism; this walks the journey they decompose — download at home, lose
the network on the hill, open the app — and it deliberately covers the
three things that survive every isolated test: **which** kind of offline
you are in, a **recycled** worker, and a **cold** start.

It is longer than the scenarios above and duplicates a few of their
assertions on purpose. Where a step has a scenario of its own, it is named
rather than repeated.

> **Read this before you judge a result**
>
> - **"Offline" is three different states**, and they exercise different
>   code (see [`offline-map.md`](offline-map.md), "Network mode"):
>   DevTools → Network → Offline is a **dead radio** (`fetch` rejects,
>   `navigator.onLine` false); the network menu's **Offline mode** switch
>   is a user instruction with the radio still **up** (`offline-forced`);
>   a network that accepts and never answers makes `fetch` **hang**, which
>   is the only state that exercises the read-path latch
>   ([`decisions/bounded-offline-read-paths.md`](decisions/bounded-offline-read-paths.md)).
>   Part B runs the first two. The third has no comfortable manual
>   recipe — a throttling profile with an enormous latency approximates it
>   — and is the weekly suite's `blackhole` mode
>   ([`offline-assurance.md`](offline-assurance.md)).
> - **"No network requests" proves nothing under DevTools Offline.** The
>   browser blocks everything, so zero is guaranteed whatever the app
>   does. The claim worth testing — that the app *declines* to spend a
>   connection it has — needs the Offline mode switch with the radio up.
>   That is Part B, step 3 — the only step here that can catch a leak.
> - **The map no longer goes blank outside a download** (SNOW-856).
>   MapLibre stretches a stored z9 tile from the shared base layer over
>   ground nobody fetched, so "it still draws" is not evidence of
>   coverage. The hatch is (SNOW-857). Judge the edge by the overlay, and
>   the bucket by the coverage probe above.

**Preconditions**

1. Signed in (Scenario 10 or 21) — *starting* a download needs an
   account (D9).
2. A clean slate: `/account/settings/` → **Reset local data** (P12).
   Note what that clears — every Cache Storage bucket including the
   **shell**, every IndexedDB database, and local/sessionStorage. It is
   not a downloads-only control; to clear only downloads, use the bin on
   each row in the sheet (D7).
3. `localStorage.removeItem('snowdesk.map.overlay.downloads')` in the
   Console. The auto-on rule in Part D fires only for a reader who has
   never touched "Display downloaded areas" — an explicit choice, either
   way, is persisted and always wins (SNOW-857). Running D2 first sets it.
4. DevTools open on **Application → Cache storage**, **Application →
   Service workers**, **Network** and **Console**.

#### Part A — prime and download

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | After the reset, reload the map **online** once | The shell re-caches. Skipping this is the most common false failure in this scenario: the reset took the shell with it, so an offline reload later fails for a reason that has nothing to do with the download |
| 2 | Switch the basemap to **Swisstopo** | Swisstopo is the sharpest subject: its style declares two vector sources, so it has two TileJSON documents to lose (P13) |
| 3 | Select CH-4115 and run the download (D1) | The roundel settles on `done` — which asserts every tile **and** every render dependency, not tiles alone |
| 4 | Run the coverage probe from the top of this section | The region bucket holds tiles at z10–14 **only**, a non-zero `glyphs` count, and a `docs` count covering the style, **both** `tiles.json` documents and the sprite. A short `docs` count is P13's subject, not a finding here |
| 5 | Look for a second new bucket | `snowdesk-basemap-pinned-base-swisstopo…` — the shared z0–9 layer (SNOW-856), topped up on the tail of the run. Without it Part D's zoom-out has nothing to draw |

#### Part B — the two kinds of offline

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | DevTools → Network → **Offline** | The header symbol goes struck-through, `data-network-state="offline"` |
| 2 | Trigger any API-backed interaction (change the date, open a region) | Requests answer **504 with `X-SW-Cache: miss`** from the worker — not `net::ERR_INTERNET_DISCONNECTED`. A native browser failure here means `_mayPassThrough` has drifted back from `_shouldUseNetwork` (SNOW-862) |
| 3 | Network → **No throttling**, then the network menu → **Offline mode** on. Clear the Network panel and use the app: pan, zoom, change date, open a region | The symbol is struck-through while `navigator.onLine` is still true, and **nothing leaves the machine**. Expected exemptions, and only these: `/sw.js` (a worker cannot intercept its own script) and `/csp/report-uri/` (the browser's policy engine, specified to bypass workers). Anything else is a leak — this is the one step here that can find one |
| 4 | Watch for a `/livez` request | None. The probe belongs to the worker's own auto-latch; a mode the user asked for is never probed out from under them |
| 5 | Panel copy: press the symbol | "Offline mode — last synced …", "You asked the app to stay offline", and a **Use the network again** button (P8) |

#### Part C — a recycled worker, and a cold start

This is the part no other scenario covers, and it is where the last two
offline defects actually shipped (SNOW-722, SNOW-854): both were found by
hand on staging while the automated suites were green.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Still offline, DevTools → Application → **Service workers** → **Stop** | The worker is terminated. Its in-memory basemap-origin allowlist dies with it |
| 2 | Reload the map | It draws. The worker rehydrates the allowlist from `meta:app`, and any tile it still cannot classify gets a read-only probe of the pinned buckets before it would reach the network. A blank map here is the SNOW-854 class of fault, not a missing download — check the buckets are still populated before blaming the download |
| 3 | Close the tab entirely. Reopen the app (installed PWA, or a new tab) with the network still down | Cold start: the shell paints from cache and the map draws. A refresh of a page that was already open never reaches this path |

#### Part D — where the coverage stops

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Look at the map without touching any switch | The coverage hatch is **on** — offline it switches itself on for a reader who has never set it (SNOW-857) — and the legend card carries the key explaining it |
| 2 | Pan to a landmark inside the hatch | Tiles draw **with place labels**. Missing labels mean the glyph promotion did not run; the tiles themselves are a separate question |
| 3 | Pan just past the hatched edge | The basemap keeps drawing, **coarsely** — stretched z9 tiles. The hatch is the only honest edge. No error page, no spinner that never ends |
| 4 | Zoom past z14, then out below z10 | Past z14 the stored tiles overzoom: larger, no new detail, never blank. Below z10 the shared base layer draws |
| 5 | Switch to a basemap you did not download under | The region roundel is a **hollow ring** in the other basemap's colour, and the downloaded ground is drawn as an **outline** rather than a hatch (SNOW-857) — "downloaded, for a different map", not data loss (D5) |

#### Part E — the things that are not tiles

A download stores basemap tiles and nothing else. Every step above can
pass while the safety data the map exists to carry is missing.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Still offline, look at the danger choropleth | It paints — `/api/ratings/` is stale-while-revalidate cached per `?d=` (P7) |
| 2 | Reload a bulletin page visited online earlier | It renders from cache with its freshness stamp |
| 3 | Navigate to a URL never visited | The branded `/static/offline.html`, with a working "Reset local data" control (P9, P12b) |
| 4 | Open a favourite whose rating is older than 48h | It reads **EXPIRED** — "Rating expired — reconnect to see today's danger level" — never a stale-looking danger chip. Weather, being non-safety, does not expire this way |
| 5 | Submit a field report, then return online | It queues offline without spending an attempt and goes out on reconnect ([`mutation-queue.md`](mutation-queue.md)) |

**What this pass does not prove.** Glyph coverage is legitimately partial
— only the ranges ordinary browsing already cached are promoted, so a
label can be missing on ground you downloaded without anything being
broken (SNOW-847). And a green run says nothing about a **hanging**
network: that is the third state above, and only `tox -e offline` covers
it.

---

## Offline, as a user meets it

D10 above is the engineering pass: it needs DevTools, the Console and a
cache probe, and it is written to localise a fault. This section answers
the question the person holding the phone actually asks — **"is this
working offline?"** — using nothing but the phone, the app and a place
with no signal.

Run it on a **real phone**, on the app installed to the home screen, on
whichever basemap you would really use. Everything below is judged by
what is on the screen. If you find yourself opening a developer tool, you
have left this script and joined D10.

> **Two things to know before you start, or you will report the wrong
> thing**
>
> - **Outside the area you downloaded, the map still draws — just
>   roughly.** Streets and place names thin out and it goes coarse, like
>   a map seen from much higher up. That is deliberate, not a fault. The
>   **shaded pattern** is what marks the ground you actually stored.
> - **A download stores the map, not the avalanche bulletin.** Danger
>   ratings and bulletin pages are kept by *visiting* them before you
>   lose signal. U1 step 6 is what sets that up, and U2 steps 7–8 check it.

### Scenario U1: The night before (at home, on wifi)

| Step | What you do | What you should see |
|------|-------------|---------------------|
| 1 | Open the app and sign in | The map |
| 2 | Find the area you are going to — search for the region or tap it on the map | Its name fills the bar at the top, with a small circular download button beside it |
| 3 | Press the download button | It fills up as it works, and squares appear on the map showing what is being stored. Nothing else demands your attention |
| 4 | Wait for it to finish | The button becomes a solid filled disc. That is the only "done" signal there is — there is no message or tick |
| 5 | Press the framed-square button at the bottom right | A panel listing what you have stored, with a size against each one and a bar showing how much of your allowance you have used |
| 6 | Look at the region you are going to, and open its bulletin page. Look at the day you are going, too | You have now *read* them, which is what keeps them available later |

### Scenario U2: On the hill (no signal)

Genuinely no signal is the real test. If you cannot get to one, put the
phone in **aeroplane mode**, which is the same thing from the app's point
of view. Doing it from your desk with wifi on is a weaker test — for that
one, use the **Offline mode** switch in the network menu
instead, which tells the app to behave as though the signal were gone.

| Step | What you do | What you should see |
|------|-------------|---------------------|
| 1 | With no signal, open the app **from the home screen icon** — not from a tab you left open | It opens to the map, as usual. Not a browser error page, not a blank screen, not a spinner that never stops |
| 2 | Look at the top of the screen | The signal symbol has a line through it. Press it and the app tells you it is offline and when it last managed to fetch anything |
| 3 | Look at the map | A shaded pattern is showing, over the ground you downloaded. You did not turn it on — offline the app puts it there, because it is the only way to see where your stored map ends. A key explaining it sits with the legend |
| 4 | Zoom into the area you downloaded | Streets, contours and **place names** all draw, at full detail, straight away |
| 5 | Pan out of the shaded area | The map keeps drawing but goes coarse. Correct — see the note above. The edge of the shading is the honest answer to "where does my map stop" |
| 6 | Zoom right in, past where you downloaded, then right out | Zoomed in, the detail stops improving and the map goes soft — it never goes blank. Zoomed out, you keep getting a whole-country view |
| 7 | Open the region you looked at last night | The bulletin and its danger rating are there, with a note saying when they were last fetched |
| 8 | Open a region you have never opened | It cannot show you a bulletin — it has never had one to keep — and says so in a sentence ("Region details are unavailable offline."). Not a spinner, not an empty panel |
| 9 | Try to start a new download | The control is visibly unavailable and says it needs a connection. It should not be hidden, and it should not fail silently after you press it |
| 10 | Leave the app for twenty minutes, then open it again, still with no signal | Everything in steps 3–7 is still true. This is the step most likely to catch a real fault: the app is more thoroughly asleep than it was, and has to rebuild more of itself |

### Scenario U3: Back in signal

| Step | What you do | What you should see |
|------|-------------|---------------------|
| 1 | Turn the signal back on (or aeroplane mode off) | Within a few seconds the symbol at the top loses its line. You should not have to reload anything |
| 2 | Look at the map | The shading turns itself off again, and the download control works once more |
| 3 | Open a bulletin | It refreshes to today's |

### What counts as a pass

Everything above happened, and at no point did you see: a browser error
page, a blank grey map inside the shading, a spinner that never resolved,
a danger rating that looked current but was days old, or a control that
did nothing when pressed.

**If something fails**, write down — before you do anything else — the
time, roughly where you were, what you had done in the app just before,
and whether the phone had partial signal rather than none. Partial signal
is a genuinely different case from none, and it is the one hardest to
reproduce later.
