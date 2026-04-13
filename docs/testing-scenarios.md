# User Testing Scenarios -- Snowdesk

> **Prerequisites**
>
> 1. Django dev server running: `poetry run python manage.py runserver` at http://localhost:8000
> 2. Mailhog running on localhost:1025 (web UI at http://localhost:8025)
> 3. Tailwind CSS watcher running: `npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch`
> 4. Database migrated: `poetry run python manage.py migrate`
> 5. Fixtures loaded: `poetry run python manage.py loaddata regions resorts`
> 6. At least one day of bulletin data fetched: `poetry run python manage.py fetch_data` (or `backfill_data --start-date 2026-04-01 --end-date 2026-04-13` for date navigation tests)

---

## Public Bulletin Site

### Scenario 1: View the marketing homepage

**Goal**: Verify the landing page loads and describes the product.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ | Page loads with heading "Snowdesk" and subtitle "Avalanche bulletins for backcountry skiers." |
| 2 | Read the feature descriptions on the page | Three features listed: "Daily bulletins", "Per-region detail", and "Season archives" |
| 3 | Locate the call-to-action button | A button labelled "View a sample bulletin" is visible at the bottom of the page |

### Scenario 2: View a random sample bulletin from the homepage

**Goal**: Verify the "View a sample bulletin" link redirects to a real bulletin page.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/ | Homepage loads |
| 2 | Click "View a sample bulletin" | Browser redirects to a URL matching `http://localhost:8000/<region_id>/<slug>/` (e.g. `http://localhost:8000/CH-4115/verbier-nendaz-veysonnaz/`) |
| 3 | Verify the bulletin page content | Page shows a region name as heading, a date label ("Today" or a formatted date), and an "issued HH:MM UTC" timestamp |
| 4 | Check for danger level | A danger level indicator is visible (e.g. "Level 2 -- Moderate") with a coloured badge |
| 5 | Check for avalanche problems | At least one key hazard is listed (e.g. "Wind slab", "New snow", or "No distinct problem") |

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

## Subscription Flow

### Scenario 10: Subscribe as a new user -- happy path

**Goal**: Complete the full subscription flow from entering an email to selecting regions.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/subscribe/ | Page loads with heading "Subscribe to avalanche bulletins" and an email input field with placeholder "your@email.com" |
| 2 | Type `tester@example.com` into the email field | Text appears in the input field |
| 3 | Click "Send magic link" | Browser redirects to http://localhost:8000/subscribe/sent/ showing "Check your inbox" and the message "the link expires in 15 minutes" |
| 4 | Open Mailhog at http://localhost:8025 | An email is listed in the inbox for `tester@example.com` |
| 5 | Open the email and click the magic link | Browser navigates to `http://localhost:8000/subscribe/verify/?token=...` and then redirects to http://localhost:8000/subscribe/manage/ |
| 6 | Verify the manage page | Page shows "Manage Subscription", the text "Bulletins sent to tester@example.com", "Your Regions" section showing "No regions selected yet", and a search box labelled "Add a Region" |

### Scenario 11: Search for a resort and add a region subscription (HTMX)

**Goal**: Use the live search to find a resort and subscribe to its region, verifying HTMX updates without page reload.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the manage page, type `Verbier` into the "Search region name" input | After a ~300ms delay, search results appear below the input without a full page reload; a result shows "Verbier (Les 4 Vallees)" with region info and an "Add" button |
| 2 | Click the "Add" button next to Verbier | The "Your Regions" section updates (without page reload) to show a card with the region name and a "Remove" button |
| 3 | Verify the search results refreshed | The search results no longer include Verbier or other resorts in the same region (Nendaz, Veysonnaz, Thyon share CH-4115) |

### Scenario 12: Add multiple regions and remove one (HTMX)

**Goal**: Verify adding and removing region subscriptions updates the UI dynamically.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the manage page, clear the search box and type `Zermatt` | Search results show "Zermatt" with region info |
| 2 | Click "Add" next to Zermatt | Zermatt's region card appears in "Your Regions"; now two region cards are listed |
| 3 | Clear the search box and type `Grindelwald` | Search results show "Grindelwald" with region info |
| 4 | Click "Add" next to Grindelwald | Grindelwald's region card appears in "Your Regions"; now three region cards are listed |
| 5 | Click "Remove" on the Zermatt region card | The Zermatt card disappears from "Your Regions" without a page reload; two region cards remain |

### Scenario 13: Unsubscribe from all regions (HTMX)

**Goal**: Verify the "Unsubscribe from all regions" button clears all subscriptions.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the manage page with at least one region subscribed, locate the "Unsubscribe from all regions" link at the bottom | The link is visible as underlined text |
| 2 | Click "Unsubscribe from all regions" | A browser confirmation dialog appears asking "Remove all region subscriptions?" |
| 3 | Click "OK" on the confirmation dialog | The "Your Regions" section updates (without page reload) to show "No regions selected yet. Use the search below to add one." |

### Scenario 14: Search with no matching results

**Goal**: Verify the empty-state message appears when no resorts match the search query.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the manage page, type `xyznonexistent` into the search box | After the 300ms delay, the search results area shows "No matching resorts found." |
| 2 | Clear the search box entirely | The search results area clears (no results, no error message) |

### Scenario 15: Search with an alternative resort name

**Goal**: Verify search matches on the alternative name field as well as the primary name.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | On the manage page, type `Matterhorn` into the search box | Search results show "Zermatt (Matterhorn)" with region info and an "Add" button |
| 2 | Clear and type `Saas Valley` | Search results show "Saas-Fee (Saas Valley)", "Saas-Grund (Saas Valley)", and "Saas-Almagell (Saas Valley)" |

### Scenario 16: Submit the email form with an invalid email address

**Goal**: Verify validation on the email form.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/subscribe/ | Email form loads |
| 2 | Type `notanemail` into the email field | Text appears in the input |
| 3 | Click "Send magic link" | The form re-renders on the same page with a validation error message below the email field (e.g. "Enter a valid email address."); the browser does NOT redirect |

### Scenario 17: Submit the email form with an empty email

**Goal**: Verify the form requires an email address.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/subscribe/ | Email form loads |
| 2 | Leave the email field empty and click "Send magic link" | The form re-renders with a validation error (e.g. "This field is required."); no redirect occurs |

### Scenario 18: Use an expired or invalid magic link

**Goal**: Verify the link-expired error page is shown for bad tokens.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/subscribe/verify/?token=expired.invalid.token | Page shows a "link has expired" message with text indicating the 15-minute validity window |
| 2 | Verify the recovery link | A link or button to request a new link is visible |
| 3 | Click the recovery link | Browser navigates to http://localhost:8000/subscribe/ (the email entry form) |

### Scenario 19: Access the magic link with no token parameter

**Goal**: Verify the verify endpoint handles a missing token gracefully.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/subscribe/verify/ | Page shows the same "link has expired" message and recovery link as Scenario 18 |

### Scenario 20: Access the manage page without authentication

**Goal**: Verify unauthenticated users are redirected to the email entry form.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Open a new private/incognito browser window | Fresh session with no cookies |
| 2 | Navigate to http://localhost:8000/subscribe/manage/ | Browser redirects to http://localhost:8000/subscribe/ (the email entry form) |

### Scenario 21: Returning subscriber re-authenticates via magic link

**Goal**: Verify a returning subscriber sees their existing regions after re-authenticating.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Complete Scenario 10 and add at least one region (e.g. Verbier / CH-4115) via Scenario 11 | Region is saved |
| 2 | Open a new private/incognito window (to clear the session) | Fresh session |
| 3 | Navigate to http://localhost:8000/subscribe/ and enter the same email (`tester@example.com`) | Magic link email is sent |
| 4 | Open Mailhog, find the new email, and click the magic link | Browser redirects to http://localhost:8000/subscribe/manage/ |
| 5 | Verify existing subscriptions | "Your Regions" section shows the previously added region (e.g. CH-4115) with a "Remove" button |

### Scenario 22: Legacy /subscribe/regions/ URL redirects to manage page

**Goal**: Verify the deprecated regions URL redirects correctly.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/subscribe/regions/ | Browser redirects to http://localhost:8000/subscribe/manage/ |

### Scenario 23: Deprecated /random/ URL redirects

**Goal**: Verify the old random URL still works via redirect.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to http://localhost:8000/random/ | Browser permanently redirects (301) to http://localhost:8000/examples/random/, which then redirects to a random bulletin page |
| 2 | Verify a bulletin loads | A bulletin detail page is displayed with a region name, date, and danger level |
