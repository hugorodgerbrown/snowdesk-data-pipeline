---
name: testing-scenarios
description: Manual test scenarios — homepage, bulletin, map, search, subscriptions, PWA install/update/offline/kill-switch — on seed_test_data data
status: current
last-reviewed: 2026-08-04
---

# User Testing Scenarios -- Snowdesk

> **Prerequisites**
>
> 1. Django dev server running: `uv run python manage.py runserver` at http://localhost:8000
> 2. Mailpit running on localhost:1025 (web UI at http://localhost:8025)
> 3. Tailwind CSS watcher running: `npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch`
> 4. Database migrated: `uv run python manage.py migrate`
> 5. Dataset seeded: `uv run python manage.py loaddata eaws_CH resorts && uv run python manage.py seed_test_data --all --commit`
>    These commands load the region/resort reference data and seed all the
>    bulletin and day-rating data needed to navigate every
>    scenario below. The
>    canonical preview URL is `/ch-4115/martigny-verbier/2026-04-08/`.

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
| 1 | Navigate to http://localhost:8000/ and open the layers menu | A "Conditions" section with a single "Weather" row, unchecked |
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
the map is already mounted behind it — and that the dismissal persists.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Clear `localStorage`, navigate to http://localhost:8000/ | Map loads with the `#home-intro` card over it |
| 2 | Click "Explore the map" | The card clears in place. **No navigation occurs** — the URL stays `http://localhost:8000/` and the map is not reloaded. The map-help coachmark tour then opens (SNOW-535) |
| 3 | Inspect `localStorage` | `snowdesk.home.intro` is set to `dismissed` |
| 4 | Reload the page | The map loads with no intro card |
| 5 | Repeat from step 1, but click the "×" close button (or press Escape) | The card dismisses and persists the same way — but the map-help tour does **not** open. That extra step is what distinguishes the CTA from the "×" |

To bring the overlay back without clearing `localStorage`, load
`http://localhost:8000/?intro=1` — it forces the panel open, survives a server
round-trip (unlike `#about`), and is stripped from the address bar on dismissal
so the panel stays dismissed across a reload. It is the handle to use in QA,
screenshots and bug reports.

---

## Subscription Flow

### Scenario 10: Subscribe as a new user -- happy path

**Goal**: Complete the full subscription flow from the bulletin-page inline form to the manage page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ch-4115/martigny-verbier/2026-04-08/ and scroll to the bottom of the bulletin | A "Get avalanche alerts" card is visible with an email input (placeholder "your@email.com") and a "Subscribe" button |
| 2 | Type `tester@example.com` into the email field | Text appears in the input field |
| 3 | Click "Subscribe" | The card is replaced in-place (HTMX, no page reload) with "Check your inbox" and "We've sent you a link to access your account. It expires in 24 hours." |
| 4 | Open Mailpit at http://localhost:8025 | An email is listed in the inbox for `tester@example.com` containing an account-access link of the form `http://localhost:8000/account/access/<token>/` |
| 5 | Open the email and click the account link | An "Access your account" confirm page loads (no auto sign-in on the GET); it shows a "Sign in to my account" button (SNOW-439) |
| 6 | Click "Sign in to my account" | Browser POSTs and redirects to http://localhost:8000/account/manage/?just_confirmed=1 |
| 7 | Verify the manage page | A "Your subscription is confirmed." banner is shown; a region card for the subscribed region (CH-4115) is listed with a "Remove" button; a "Passkeys" section prompts "Sign in faster with a passkey" |

### Scenario 11: One-click add a region from another bulletin page (HTMX)

**Goal**: Verify the inline CTA becomes a one-click "Add region" button when signed in, updating without a page reload.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in (Scenario 10), navigate to http://localhost:8000/CH-1221/grindelwald/2026-04-10/ and scroll to the CTA | The card shows "You're signed in. One click to add daily bulletin updates for this region." with an "Add region" button (no email input) |
| 2 | Click "Add region" | The card is replaced in-place (no page reload) with a confirmation that the region was added, including a "Manage your subscriptions" link |
| 3 | Navigate to http://localhost:8000/account/manage/ | Two region cards are listed |

### Scenario 12: Add multiple regions and remove one (HTMX)

**Goal**: Verify adding and removing region subscriptions updates the UI dynamically.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in, navigate to http://localhost:8000/CH-4222/ and scroll to the CTA | The bulletin page for the Zermatt region shows the one-click "Add region" card |
| 2 | Click "Add region" | The card confirms the region was added |
| 3 | Navigate to http://localhost:8000/account/manage/ | Three region cards are listed |
| 4 | Click "Remove" on the Grindelwald region card | The card disappears without a page reload (HTMX swap); two region cards remain |

### Scenario 13: Unsubscribe from all alerts (delete account)

**Goal**: Verify the "Unsubscribe from all alerts" button deletes the account.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the manage page with at least one region subscribed, locate the "Unsubscribe from all alerts" link at the bottom | The link is visible as small underlined text below the Passkeys section |
| 2 | Click "Unsubscribe from all alerts" | A browser confirmation dialog appears asking "Unsubscribe from all alerts and delete your account?" |
| 3 | Click "OK" on the confirmation dialog | Browser is redirected to http://localhost:8000/account/unsubscribe-done/; the subscriber account is hard-deleted and the session is cleared |

### Scenario 14: Removing the last region deletes the account

**Goal**: Verify the last-region cascade hard-deletes the subscriber.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Subscribe to exactly one region (Scenario 10) and open http://localhost:8000/account/manage/ | One region card is listed |
| 2 | Click "Remove" on the only region card | Browser is redirected to http://localhost:8000/account/unsubscribe-done/ |
| 3 | Navigate to http://localhost:8000/account/manage/ | Browser redirects to http://localhost:8000/account/sign-in/ (account deleted, session cleared) |

### Scenario 15: Already-subscribed region shows an Unsubscribe CTA

**Goal**: Verify the inline CTA reflects an existing subscription and can remove it.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in and subscribed to CH-4115, navigate to http://localhost:8000/ch-4115/martigny-verbier/2026-04-08/ and scroll to the CTA | The card reads "You receive daily bulletin updates for this region." with an "Unsubscribe" button and a "Manage your subscriptions" link |
| 2 | Click "Unsubscribe" (with at least one other region still subscribed) | The card is replaced in-place with an unsubscribed confirmation, without a page reload |

### Scenario 16: Submit the inline subscribe form with an invalid email address

**Goal**: Verify validation on the inline email form.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Sign out (or use a fresh private window) and open http://localhost:8000/ch-4115/martigny-verbier/2026-04-08/ | The "Get avalanche alerts" card with the email input loads |
| 2 | Type `notanemail` into the email field | Text appears in the input |
| 3 | Click "Subscribe" | Submission is blocked: either the browser's built-in email validation fires, or the form re-renders in place with a validation error (e.g. "Enter a valid email address."); the page does NOT navigate |

### Scenario 17: Submit the inline subscribe form with an empty email

**Goal**: Verify the form requires an email address.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the same bulletin page, leave the email field empty and click "Subscribe" | Submission is blocked: either the browser's required-field validation fires, or the form re-renders in place with a validation error (e.g. "This field is required."); no navigation occurs |

### Scenario 18: Use an expired or invalid account link

**Goal**: Verify the link-expired error page is shown for bad tokens.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/access/expired.invalid.token/ | Page shows "This link has expired" (HTTP 400) with the text "Account links are only valid for 24 hours. This one has expired or is invalid." |
| 2 | Verify the recovery link | A "Request a new link" button is visible |
| 3 | Click "Request a new link" | Browser navigates to http://localhost:8000/account/manage/, which redirects (unauthenticated) to http://localhost:8000/account/sign-in/ |

### Scenario 19: Access the account URL with no token

**Goal**: Verify the account endpoint requires a token in the URL path.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/access/ | Browser shows a 404 Not Found page (the URL pattern requires a token segment) |

### Scenario 20: Access the manage page without authentication

**Goal**: Verify unauthenticated users are redirected to the sign-in page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Open a new private/incognito browser window | Fresh session with no cookies |
| 2 | Navigate to http://localhost:8000/account/manage/ | Browser redirects to http://localhost:8000/account/sign-in/ |

### Scenario 21: Returning subscriber re-authenticates via the sign-in page

**Goal**: Verify a returning subscriber sees their existing regions after re-authenticating.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Complete Scenario 10 (subscribe and confirm at least one region, e.g. CH-4115) | Region is saved |
| 2 | Open a new private/incognito window (to clear the session) | Fresh session |
| 3 | Navigate to http://localhost:8000/account/sign-in/ | Page loads with a "Sign in" heading, the text "Enter your email address and we'll send you a sign-in link.", and (where WebAuthn is available) a "Sign in with a passkey" button |
| 4 | Enter `tester@example.com` and click "Send sign-in link" | A "Check your inbox" page loads: "If that address is registered, we've sent you a link to manage your subscriptions. It expires in 24 hours." (the same response is shown whether or not the email is registered) |
| 5 | Open Mailpit, find the new email, and click the account link | Browser redirects to http://localhost:8000/account/manage/ |
| 6 | Verify existing subscriptions | The previously added region card (e.g. CH-4115) is listed with a "Remove" button |

### Scenario 22: Sign out via the nav account menu

**Goal**: Verify the authenticated account menu offers sign-out.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in, click the circular avatar button (first letter of your email) in the top nav | A dropdown menu opens listing the subscribed region links, a "Manage alerts" link, and a "Sign out" button |
| 2 | Click "Sign out" | The session is cleared and the browser is redirected to the sign-in page; navigating to http://localhost:8000/account/manage/ now redirects to http://localhost:8000/account/sign-in/ |

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

> Automated: [test_pwa_lifecycle_install.py::test_first_install_registers_and_caches_shell](../tests/e2e/test_pwa_lifecycle_install.py)

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
> `.accepted` / `.dismissed` / `.completed`) IS covered — see
> [test_pwa_client_signals.py](../tests/e2e/test_pwa_client_signals.py).

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

> Automated: [test_pwa_lifecycle_update.py::test_update_banner_appears_on_new_sw_bytes](../tests/e2e/test_pwa_lifecycle_update.py)
> — drives the byte-diff via a server-side monkeypatch of
> `apps.public.views._serve_sw_file` rather than DevTools' "Update" button;
> Playwright cannot observe or intercept a service worker's own script
> fetch (confirmed during the SNOW-389 spike — see
> [_spike_results.py](../tests/e2e/_spike_results.py)).

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
| 2 | Look at the page | The `#sw-update-banner` slides in at the bottom-centre: refresh icon, "Update available", "A newer version of Snowdesk is ready. Your downloaded maps and saved data are kept.", and a "Reload" primary CTA + `×` |
| 3 | Click "Reload" on the banner | The banner button briefly disables; the waiting worker activates (Service workers panel: the new SW becomes `activated and is running`, the old one disappears); the page reloads exactly once onto the new shell (URL and content preserved) |
| 4 | Reload one more time | No banner appears — you are already on the latest version |

### Scenario P5: Update banner via server X-App-Version drift (header path)

> Automated: [test_pwa_lifecycle_update.py::test_header_drift_shows_banner_and_clears_shell_caches](../tests/e2e/test_pwa_lifecycle_update.py)
> — the header drift is injected via `page.route()` on one fetch rather
> than restarting the server with `APP_VERSION` overridden; the reload's
> cache wipe is proven by planting a cache-entry marker and observing it
> gone afterwards.

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
reveals nothing (automated:
[test_pwa_lifecycle_update.py::test_stale_cached_header_does_not_show_banner](../tests/e2e/test_pwa_lifecycle_update.py)).
This is the fix for the staging stuck-banner bug, where Reload could
never clear a banner triggered by stale cached headers.

### Scenario P6: Forced update via APP_BLOCKED_VERSIONS

> Automated: [test_pwa_lifecycle_update.py::test_blocked_build_shows_modal_and_waits_for_the_click](../tests/e2e/test_pwa_lifecycle_update.py)

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
| 1 | In the still-open tab, trigger any request (scroll the timeline, tap a region) | DevTools → Network: one `no-store` request to `/api/version` whose body reads `"update_required": true`; `#pwa-update-modal` opens as a full-viewport overlay with "Update required" copy naming what the reload keeps, and a single "Reload now" CTA; no dismiss control; underlying page scroll is locked |
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
> [test_scrubber_reverse.py](../tests/e2e/test_scrubber_reverse.py) for
> the dedicated scrubber-UI coverage).

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
| 5 | Sign in, open the account menu and switch **Offline mode** on | The switch is the first row, above "Subscriptions"; the symbol goes struck-through while `navigator.onLine` is still true, and the panel now reads "Offline mode — last synced …" with "You asked the app to stay offline" and a **Use the network again** button |
| 6 | Sign out and repeat step 5's state via the worker's own latch (three failed reads) | The switch is absent — it is signed-in only — but the panel still offers **Try reconnecting**, which is an anonymous reader's only way back |
| 7 | With the panel open, press its "×" (top-right), then reopen it and press Escape | Each closes the panel and returns focus to the symbol; the "×" is a full 44×44 target, not a hairline glyph |

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

> Automated: [test_pwa_lifecycle_kill_and_reset.py::test_kill_switch_a_prevents_registration](../tests/e2e/test_pwa_lifecycle_kill_and_reset.py)
> — `/api/sw-config` is routed to `kill: true` via `page.route()` on a
> fresh tab rather than restarting the server with `SW_KILL=true`.

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
> per the scope's fallback ladder ("flaky > absent, but flaky < manual")
> — see [_spike_results.py](../tests/e2e/_spike_results.py).
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

### Scenario P12: Reset local data (manage page button)

> Automated: [test_pwa_lifecycle_kill_and_reset.py::test_manage_page_reset_local_data](../tests/e2e/test_pwa_lifecycle_kill_and_reset.py)
> — correction from implementation: `[data-pwa-reset-trigger]` on the
> manage page is bound by `pwa_reset.js`, which gates on a native
> `window.confirm()` dialog, not the `#pwa-reset-required` overlay. That
> overlay is a distinct, unrelated mechanism — `db.js`'s terminal Reset
> Required state after an IndexedDB migration failure — and is never
> shown by this button.

**Goal**: Verify the "Reset local data" control on the manage page
clears IndexedDB + Cache storage + unregisters the SW.

**Preconditions**: Signed in (Scenario 10 or 21). Scenario P1
completed so state exists to clear.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/account/manage/ and locate the "Reset local data" button in the account section | Button is visible with a short explanation of what it does |
| 2 | Click "Reset local data" | A native confirm dialog opens, summarising what will and won't be cleared |
| 3 | Accept the dialog | Application → IndexedDB (`snowdesk-pwa-v1`), Cache storage (`snowdesk-shell-*`), and Service workers are all cleared; page reloads and re-registers a fresh SW |

### Scenario P12b: Reset local data (offline fallback page)

> Automated: [test_pwa_lifecycle_kill_and_reset.py::test_offline_page_reset_control](../tests/e2e/test_pwa_lifecycle_kill_and_reset.py).
> The offline half of the journey (step 4) is covered by
> `test_offline_page_reset_control_offline` in the same file — see
> [`offline-first.md`](offline-first.md#reset-local-data-snow-378).

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
