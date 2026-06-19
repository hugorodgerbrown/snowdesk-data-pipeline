---
name: testing-scenarios
description: Manual user-testing scenarios — homepage, bulletin pages, map, search and subscription flows on the test_data fixture
status: current
last-reviewed: 2026-06-10
---

# User Testing Scenarios -- Snowdesk

> **Prerequisites**
>
> 1. Django dev server running: `uv run python manage.py runserver` at http://localhost:8000
> 2. Mailhog running on localhost:1025 (web UI at http://localhost:8025)
> 3. Tailwind CSS watcher running: `npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch`
> 4. Database migrated: `uv run python manage.py migrate`
> 5. Fixtures loaded: `uv run python manage.py loaddata test_data`
>    This single command loads all region, resort, bulletin, day-rating, and
>    weather-snapshot data needed to navigate every scenario below. The
>    canonical preview URL is `/ch-4115/martigny-verbier/2026-04-08/`.

---

## Public Bulletin Site

### Scenario 1: View the marketing homepage

**Goal**: Verify the landing page loads and describes the product.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ | Page loads with heading "Snowdesk" and subtitle "Avalanche bulletins for backcountry skiers." |
| 2 | Look at the page chrome | A thin top nav bar with a "Snowdesk" wordmark (no back link) sits above the content |
| 3 | Read the feature descriptions on the page | Three features listed: "Daily bulletins", "Per-region detail", and "Season archives" |
| 4 | Locate the call-to-action buttons | Two side-by-side buttons are visible: "View a sample bulletin →" (dark fill) and "Explore the map →" (light fill, outlined) |

### Scenario 2: View a random sample bulletin from the homepage

**Goal**: Verify the "View a sample bulletin" link shows a random bulletin inline.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ | Homepage loads |
| 2 | Click "View a sample bulletin" | A bulletin page loads at `http://localhost:8000/examples/random/` (URL stays the same, no redirect) |
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
| 2 | Navigate to http://localhost:8000/map/ | Same nav bar visible above the map frame; no back link |
| 3 | Navigate to http://localhost:8000/CH-4115/verbier/ | Nav bar shows a left-chevron + "Map" link, a thin vertical divider, and the "Snowdesk" wordmark at a smaller size (15px) |
| 4 | Navigate to http://localhost:8000/CH-4115/season/ | Nav bar again shows "← Map" and the wordmark |
| 5 | Navigate to http://localhost:8000/CH-4115/ (random/recent bulletins) | Nav bar again shows "← Map" and the wordmark |
| 6 | Click the "Snowdesk" wordmark from any of the pages above | Browser navigates to http://localhost:8000/ |
| 7 | From a bulletin page, click the "← Map" back link | Browser navigates to http://localhost:8000/map/ |

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

**Goal**: Verify the /map/ page renders all regions, colours them by today's
danger rating, and shows the legend.

**Preconditions**: Bulletins for today have been fetched
(`uv run python manage.py fetch_bulletins --source slf --date $(date +%Y-%m-%d) --commit`)
and regions/resorts fixtures are loaded.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/map/ | MapLibre basemap of Switzerland renders centred on the Alps; Swiss region polygons are overlaid and filled in danger-level colours |
| 2 | Locate the legend | A "Today" legend is visible with four swatch rows: "Low (1)" green-yellow, "Moderate (2)" yellow, "Considerable (3)" orange, "High (4)" red |
| 3 | Locate the hint text | Footer hint reads "Pinch to zoom · tap a region" |
| 4 | Check regions without today's bulletin | Rendered in grey (no_rating colour) — not missing |
| 5 | Zoom in past zoom level ~8.5 | Region labels appear (region names rendered in the polygon centres); zoom back out and they disappear |

### Scenario M2: Tap a region to open the bottom sheet

**Goal**: Verify clicking a region surfaces today's danger rating, linked
resorts, and a working CTA to the bulletin page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On http://localhost:8000/map/, click a region with a bulletin (e.g. Verbier / CH-4115) | A bottom sheet slides up from the page bottom |
| 2 | Read the sheet title | Shows the region name (e.g. "Verbier") |
| 3 | Read the sheet body | Shows today's danger rating and any linked resort names |
| 4 | Locate the CTA | "Read today's bulletin →" link visible at the bottom of the sheet |
| 5 | Click the CTA | Browser navigates to the region's bulletin page for today (e.g. `/CH-4115/verbier/`) |
| 6 | Return to /map/ and click a different region | Sheet content updates; the newly selected region's outline thickens and darkens |
| 7 | Click the × close button on the sheet | Sheet dismisses; the region's selection outline is removed |

### Scenario M3: Regions with no bulletin render as no_rating

**Goal**: Verify regions absent from `/api/ratings/` show as grey
and the sheet reflects that.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | In the Django shell, delete today's bulletins for one region (e.g. `RegionBulletin.objects.filter(region__region_id='CH-4115', ...)`) | Bulletins removed |
| 2 | Reload http://localhost:8000/map/ | That region renders in grey |
| 3 | Click the region | Sheet opens; danger rating shows as "no rating" / unavailable |

### Scenario M4: Debug mode reveals region IDs

**Goal**: Verify `?debug=1` or pressing `d` on the page reveals the debug pill
and region IDs in the sheet.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/map/?debug=1 | Map loads; a small "debug · press d" pill visible top-right |
| 2 | Click a region | Sheet shows the SLF region identifier (e.g. "CH-4115") in a monospace debug line beneath the region name |
| 3 | Navigate to http://localhost:8000/map/ (without the debug flag) | Debug pill is hidden |
| 4 | Press `d` on the keyboard | Debug pill appears and the sheet's region-id line becomes visible |

---

## Map Search

### Scenario MS1: Search for a region by name

**Goal**: Verify the autocomplete finds a Swiss avalanche region by name.

**Preconditions**: /map/ has fully loaded (regions + resorts + summaries).

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On http://localhost:8000/map/, click into the search input placeholder "Find a region or resort" | Input receives focus |
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

### Scenario MS7: Homepage CTA opens the map

**Goal**: Verify the "Explore the map" homepage CTA routes to the map page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ | Homepage loads with two CTAs side by side |
| 2 | Click "Explore the map →" | Browser navigates to http://localhost:8000/map/ and the choropleth loads |

---

## Subscription Flow

### Scenario 10: Subscribe as a new user -- happy path

**Goal**: Complete the full subscription flow from the bulletin-page inline form to the manage page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ch-4115/martigny-verbier/2026-04-08/ and scroll to the bottom of the bulletin | A "Get avalanche alerts" card is visible with an email input (placeholder "your@email.com") and a "Subscribe" button |
| 2 | Type `tester@example.com` into the email field | Text appears in the input field |
| 3 | Click "Subscribe" | The card is replaced in-place (HTMX, no page reload) with "Check your inbox" and "We've sent you a link to access your account. It expires in 24 hours." |
| 4 | Open Mailhog at http://localhost:8025 | An email is listed in the inbox for `tester@example.com` containing an account-access link of the form `http://localhost:8000/subscribe/account/<token>/` |
| 5 | Open the email and click the account link | Browser redirects to http://localhost:8000/subscribe/manage/?just_confirmed=1 |
| 6 | Verify the manage page | A "Your subscription is confirmed." banner is shown; a region card for the subscribed region (CH-4115) is listed with a "Remove" button; a "Passkeys" section prompts "Sign in faster with a passkey" |

### Scenario 11: One-click add a region from another bulletin page (HTMX)

**Goal**: Verify the inline CTA becomes a one-click "Add region" button when signed in, updating without a page reload.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in (Scenario 10), navigate to http://localhost:8000/CH-1221/grindelwald/2026-04-10/ and scroll to the CTA | The card shows "You're signed in. One click to add daily bulletin updates for this region." with an "Add region" button (no email input) |
| 2 | Click "Add region" | The card is replaced in-place (no page reload) with a confirmation that the region was added, including a "Manage your subscriptions" link |
| 3 | Navigate to http://localhost:8000/subscribe/manage/ | Two region cards are listed |

### Scenario 12: Add multiple regions and remove one (HTMX)

**Goal**: Verify adding and removing region subscriptions updates the UI dynamically.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in, navigate to http://localhost:8000/CH-4222/ and scroll to the CTA | The bulletin page for the Zermatt region shows the one-click "Add region" card |
| 2 | Click "Add region" | The card confirms the region was added |
| 3 | Navigate to http://localhost:8000/subscribe/manage/ | Three region cards are listed |
| 4 | Click "Remove" on the Grindelwald region card | The card disappears without a page reload (HTMX swap); two region cards remain |

### Scenario 13: Unsubscribe from all alerts (delete account)

**Goal**: Verify the "Unsubscribe from all alerts" button deletes the account.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the manage page with at least one region subscribed, locate the "Unsubscribe from all alerts" link at the bottom | The link is visible as small underlined text below the Passkeys section |
| 2 | Click "Unsubscribe from all alerts" | A browser confirmation dialog appears asking "Unsubscribe from all alerts and delete your account?" |
| 3 | Click "OK" on the confirmation dialog | Browser is redirected to http://localhost:8000/subscribe/unsubscribe-done/; the subscriber account is hard-deleted and the session is cleared |

### Scenario 14: Removing the last region deletes the account

**Goal**: Verify the last-region cascade hard-deletes the subscriber.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Subscribe to exactly one region (Scenario 10) and open http://localhost:8000/subscribe/manage/ | One region card is listed |
| 2 | Click "Remove" on the only region card | Browser is redirected to http://localhost:8000/subscribe/unsubscribe-done/ |
| 3 | Navigate to http://localhost:8000/subscribe/manage/ | Browser redirects to http://localhost:8000/subscribe/sign-in/ (account deleted, session cleared) |

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
| 1 | Navigate to http://localhost:8000/subscribe/account/expired.invalid.token/ | Page shows "This link has expired" (HTTP 400) with the text "Account links are only valid for 24 hours. This one has expired or is invalid." |
| 2 | Verify the recovery link | A "Request a new link" button is visible |
| 3 | Click "Request a new link" | Browser navigates to http://localhost:8000/subscribe/manage/, which redirects (unauthenticated) to http://localhost:8000/subscribe/sign-in/ |

### Scenario 19: Access the account URL with no token

**Goal**: Verify the account endpoint requires a token in the URL path.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/subscribe/account/ | Browser shows a 404 Not Found page (the URL pattern requires a token segment) |

### Scenario 20: Access the manage page without authentication

**Goal**: Verify unauthenticated users are redirected to the sign-in page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Open a new private/incognito browser window | Fresh session with no cookies |
| 2 | Navigate to http://localhost:8000/subscribe/manage/ | Browser redirects to http://localhost:8000/subscribe/sign-in/ |

### Scenario 21: Returning subscriber re-authenticates via the sign-in page

**Goal**: Verify a returning subscriber sees their existing regions after re-authenticating.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Complete Scenario 10 (subscribe and confirm at least one region, e.g. CH-4115) | Region is saved |
| 2 | Open a new private/incognito window (to clear the session) | Fresh session |
| 3 | Navigate to http://localhost:8000/subscribe/sign-in/ | Page loads with a "Sign in" heading, the text "Enter your email address and we'll send you a sign-in link.", and (where WebAuthn is available) a "Sign in with a passkey" button |
| 4 | Enter `tester@example.com` and click "Send sign-in link" | A "Check your inbox" page loads: "If that address is registered, we've sent you a link to manage your subscriptions. It expires in 24 hours." (the same response is shown whether or not the email is registered) |
| 5 | Open Mailhog, find the new email, and click the account link | Browser redirects to http://localhost:8000/subscribe/manage/ |
| 6 | Verify existing subscriptions | The previously added region card (e.g. CH-4115) is listed with a "Remove" button |

### Scenario 22: Sign out via the nav account menu

**Goal**: Verify the authenticated account menu offers sign-out.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | While signed in, click the circular avatar button (first letter of your email) in the top nav | A dropdown menu opens listing the subscribed region links, a "Manage alerts" link, and a "Sign out" button |
| 2 | Click "Sign out" | The session is cleared and the browser is redirected to the sign-in page; navigating to http://localhost:8000/subscribe/manage/ now redirects to http://localhost:8000/subscribe/sign-in/ |

### Scenario 23: Deprecated /random/ URL redirects

**Goal**: Verify the old random URL still works via redirect.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/random/ | Browser permanently redirects (301) to http://localhost:8000/examples/random/ |
| 2 | Verify a bulletin loads | A random bulletin renders inline at the `/examples/random/` URL with a region name, date, and danger level |
