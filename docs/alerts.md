---
name: alerts
description: Bulletin alerts — send_bulletin_alerts, BulletinAlert, notify_when, trip_places, /alerts/<day>/, the Today sun's dot, bulletin_alerts flag
status: current
last-reviewed: 2026-11-01
---

# Bulletin alerts

One message per person per run, naming every place whose bulletin is new
or has changed. Push first; email when no device can show a notification,
or always if the person asks. Phase A of the roadmap (SNOW-937).

**Ships dark.** The settings, the region-panel prompt and the since strip
are behind the `bulletin_alerts` flag (superusers). The scheduled sender
is behind the `schedule_send_bulletin_alerts` switch, which deploys off.
See "Going public" before turning either on.

## The pieces

| Step | What | Code |
|------|------|------|
| State | What a reader would notice in a bulletin: level, subdivision, problems with aspects, elevation band and rating. Version-stamped JSON and a 16-char fingerprint, computed once by the service and stored in the render model's `derived.state` (SNOW-1341) | `apps/ingest/normalise/bulletin_state.py` — `BulletinState`, `bulletin_state`; `state_of` (the stored state, `apps.ingest.public`); `apps/bulletins/services/bulletin_change.py` re-exports them and holds `summarise_change` |
| Decision | Whether a (place, day) sends, is skipped, or is recorded silently | `apps/bulletins/services/alerts.py` — `decide` (pure) |
| Words | Titles and bodies, one place or a bundle | `apps/bulletins/services/alert_wording.py` — `alert_message` |
| Run | Per account: evaluate every place, one message, deliver, log | `alerts.evaluate_account`, `alerts.deliver`; `send_bulletin_alerts` |
| Log | One `BulletinAlert` row per (account, region, day, state). The previous row is the "last told" state, so the log *is* the change memory | `apps/bulletins/models.py::BulletinAlert` |
| Email | Sent from `ALERTS_FROM_EMAIL` via a django-tasks worker, with one-click `List-Unsubscribe` | `apps/accounts/services/alert_email.py` |
| Landing | A bundle lands on `/alerts/<day>/`; a one-place alert on the map's region panel (`/map/?d=…#region`) | `apps/public/alert_views.py`, `public/alerts_day.html` |
| Since strip | "Since Friday" sentences at the top of the region body, same words as the alert | `templates/includes/_since_strip.html`, `public.views._since_strip` |
| Settings | Mode, email, this device, and a switch per place | `apps/accounts/alert_views.py`, `_alert_settings.html`, `alerts_optin.js` |
| Dot | The Today sun in the map's date row shows a red dot (per device) while the newest message of the past week is one this device has not opened Today since; drawn only for an account with alerts on. The bell that listed the messages was retired by SNOW-1366 | `alerts.recent_alerts`, `accounts:alert_recent` (`/account/alerts/recent/`), `alert_views.alert_list_url`, `static/js/today_panel.js` |
| Replay | A real season's alerts for one region, in delivery order | `services/replay.py`, `replay_bulletin_alerts`, staff `/_alerts-replay/` |

## Rules

- **Off until chosen.** `Account.notify_when` defaults to `OFF`. Email is
  the fallback, so a default of on would email people who never asked.
  Turning alerts on stamps `alerts_enabled_at`; a day already under way
  then is recorded as `BASELINE` rather than announced.
- **Which places.** Region pins and saved resorts, each with
  `Favourite.alerts` (default on). A dropped pin never alerts. Places in
  one region are one place, named "Martigny-Verbier, Verbier".
- **Saved trips (SNOW-947, SNOW-945).** A trip the account has saved
  (organiser or not, `Trip.objects.for_user`) is a place on its own day
  only, covering every region its route crosses — `track_regions`, the
  rule the trip page and the Plan page use, so the alert covers what the
  trip page shows. `trip_places` builds them and `places_on` folds them
  into the day's pins. A trip that stays in one region is a place in that
  region, and in a pinned region it is one place and one row, with both
  names. A trip crossing several regions is one place of its own
  (`AlertPlace.regions`, keyed by the trip page): it writes one send-log
  row per region, fires when any of them does, and reads "For your trip.
  N bulletins." with no level. A one-place trip alert, and the trip's row
  on `/alerts/<day>/`, open the trip page. A track outside every region
  adds nothing. Trips follow the account's alert setting; there is no
  per-trip switch
  ([why](decisions/a-route-carries-the-regions-it-crosses.md)).
- **Which bulletin.** `RegionDayRating.source_bulletin` — the same issue
  the map shows for that day.
- **Modes.** `NEW_BULLETIN` restates the establishing issue every day;
  `CHANGES` sends only when the level moved or a problem came or went
  from the day before — except
  on a saved trip's day, whose establishing issue always sends (Hugo,
  2026-10-30: the night before you go, you want the bulletin). A pin's
  quiet day logged `SKIPPED` before the trip was saved does not count as
  the trip's alert. An update
  to a day already told sends in either mode when the level moved or a
  problem came or went. A reissue a reader
  could not tell apart (same fingerprint) never sends.
- **The day, the level and what moved (SNOW-1356).** A one-place
  alert's title names the place and the day the bulletin is for, so
  Wednesday's 17:00 issue says Thursday: "Verbier – Thursday bulletin";
  a reissue is "Verbier – Thursday bulletin update". The body is the
  level and whether it moved: "Considerable, up from Moderate.",
  "Considerable (3+), up from 3−." within a level, "Moderate, avalanche
  problems changed." (a problem added or removed), "Moderate, no
  change." A problem only shifting its aspects, band or rating is no
  change (`BulletinChange.is_notable`): SLF shifts one most days, and in a
  replayed season it fired "problems changed" on two days in five. A
  season start leads "First of the season."; a restart after a pause
  inside the season (a bulletin within `SEASON_GAP_DAYS`, 60, before
  yesterday, `had_bulletin_in_season`) leads "Bulletins are back."; a
  trip leads "For your trip.". SNOW-1332's open
  door ("Verbier bulletin" / "Valid from … to …") read as the same message
  every day of a replayed season (Hugo, 2026-11-01). Problems, aspects
  and heights stay on the page the tap opens. A bundle is unchanged:
  "Bulletins for your 3 places" / "Chamonix, Davos and Verbier. Valid
  from …", "and N more" past four. The email carries the same words. The
  full sentences (`change_sentences`) stay for the since strip and
  `/alerts/<day>/`.
- **New snow (SNOW-944).** A one-place alert ends "20 cm new snow
  forecast." when the `Weather` row for the bulletin's day at the region's
  `centroid_location` forecasts 5 cm or more (`alerts.forecast_new_snow`:
  that day's own row and its `snowfall_sum`, refined by every fetch until
  the day passes, SNOW-1337; `NEW_SNOW_MIN_CM`). The replay archive
  carries no weather, so `/_alerts-replay/` names none unless
  `replay_region(new_snow=…)` is given it.
- **One day per place.** If today and tomorrow both fire for one place,
  tomorrow is the message and today is logged `SKIPPED`.
- **Night hold.** 22:00–06:30 Europe/Zurich nothing is sent; the 06:30
  run picks up what was held.
- **Reach.** `deliver` writes the rows `PENDING` and queues one worker
  (`_worker_deliver_alert`). It pushes to every active `PushSubscription`
  first, then emails from what actually happened: `ALWAYS` emails anyway,
  `FALLBACK` emails when no push was accepted (no device, or every one
  refused — an expired device's 410 included), `NEVER` never does, and
  never to an unverified account. The rows become `SENT`, or `FAILED` when
  nothing reached the person. A `FAILED` row is not counted as told, so the
  next run sends again. With no possible channel at all the rows are
  logged `SKIPPED`, so the state still advances.

## Going public

Before the switch goes on:

1. Confirm the email provider and SPF, DKIM and DMARC for the alerts@
   sender in `ALERTS_FROM_EMAIL`.
2. Read a replayed season (`/_alerts-replay/`, both modes).
3. Widen the `bulletin_alerts` flag, then turn on
   `schedule_send_bulletin_alerts`.
4. In the same change: flip the alerts row on `/compare/` (SNOW-943),
   change the help page's "Snowdesk sends no bulletin alerts", and narrow
   `tests/public/test_no_bulletin_alert_claims.py`, which holds every
   other page to the old truth until then.

Deferred: the NEW tag on
cards.
