---
name: the-bell-lists-your-alerts
description: Why the map bell lists your past week's alerts only (map_alerts.js, #map-alerts, alert_recent, recent_alerts, alert_list_url)
status: current
last-reviewed: 2026-10-30
---

# The bell lists your alerts

**SNOW-1333.** The bell in the map's date row (`#map-alerts`,
`static/js/map_alerts.js`) lists the bulletin alerts sent to the account
in the past seven days, newest first: each message's own title and body,
when it went, and a link to where its push went. It replaced the day's
notes (SNOW-1206).

- **Only for an account with alerts on.** `alert_list_url` gives the page
  `/account/alerts/recent/` only when the person is signed in, the
  `bulletin_alerts` flag is on for them, they have an `Account` and
  `notify_when` is not `OFF`. Otherwise the bell is not in the page at all,
  so a visitor makes no request.
- **Shown only when there is one.** An empty week, or a failed read, leaves
  the bell hidden.
- **The dot is per device.** It shows while the newest alert's `sent_at` is
  later than the value under `localStorage['snowdesk:alerts-seen']`, and
  opening the list writes it. No server state, no migration.
- **One message, one entry.** A bundle writes one `BulletinAlert` row per
  place with the same words; `recent_alerts` folds consecutive identical
  messages into one.

## Why

Hugo, on the alerts going live: the bell with the red dot should be a list
of these notifications, not anything else. A red dot on a bell reads as
"something was sent to you" everywhere else on a phone; spending it on
facts about the map's day (no bulletin, the weather forecast does not
reach this far) taught the opposite. Those facts were dropped, not moved:
the layers menu's Weather row still says "Forecast only", and the date
control already shows which days have a bulletin.

The answers he gave when it was scoped: the notes are dropped; signed out
or alerts off, no bell; one week of history; the dot by whichever means is
simplest, which is the device's own storage.

## Consequences

- `/account/alerts/recent/` is `never_cache` and the service worker leaves
  it network-only, so offline the bell stays hidden rather than showing a
  stale list. The links themselves are ordinary pages.
- The list is what was *sent* (`outcome=SENT`), not what was decided:
  skipped and baseline rows never appear.
- A dot cleared on a phone still shows on the laptop. That is the cost of
  per-device storage, accepted because it needs nothing on the server.
- Nothing else writes to the bell. A new fact about the day goes on the
  surface it is about, not behind the bell.
