---
name: alerts
description: Bulletin alerts — send_bulletin_alerts, BulletinAlert, BulletinState, notify_when, /alerts/<day>/, since strip, bulletin_alerts flag
status: current
last-reviewed: 2026-10-30
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
| State | What a reader would notice in a bulletin: level, subdivision, problems with aspects, elevation band and rating. Version-stamped JSON and a 16-char fingerprint | `apps/bulletins/services/bulletin_change.py` — `BulletinState`, `bulletin_state`, `summarise_change` |
| Decision | Whether a (place, day) sends, is skipped, or is recorded silently | `apps/bulletins/services/alerts.py` — `decide` (pure) |
| Words | Titles and bodies, one place or a bundle | `apps/bulletins/services/alert_wording.py` — `alert_message` |
| Run | Per account: evaluate every place, one message, deliver, log | `alerts.evaluate_account`, `alerts.deliver`; `send_bulletin_alerts` |
| Log | One `BulletinAlert` row per (account, region, day, state). The previous row is the "last told" state, so the log *is* the change memory | `apps/bulletins/models.py::BulletinAlert` |
| Email | Sent from `ALERTS_FROM_EMAIL` via a django-tasks worker, with one-click `List-Unsubscribe` | `apps/accounts/services/alert_email.py` |
| Landing | A bundle lands on `/alerts/<day>/`; a one-place alert on the map's region panel (`/map/?d=…#region`) | `apps/public/alert_views.py`, `public/alerts_day.html` |
| Since strip | "Since Friday" sentences at the top of the region body, same words as the alert | `templates/includes/_since_strip.html`, `public.views._since_strip` |
| Settings | Mode, email, this device, and a switch per place | `apps/accounts/alert_views.py`, `_alert_settings.html`, `alerts_optin.js` |
| Replay | A real season's alerts for one region, in delivery order | `services/replay.py`, `replay_bulletin_alerts`, staff `/_alerts-replay/` |

## Rules

- **Off until chosen.** `Account.notify_when` defaults to `OFF`. Email is
  the fallback, so a default of on would email people who never asked.
  Turning alerts on stamps `alerts_enabled_at`; a day already under way
  then is recorded as `BASELINE` rather than announced.
- **Which places.** Region pins and saved resorts, each with
  `Favourite.alerts` (default on). A dropped pin never alerts. Places in
  one region are one place, named "Martigny-Verbier, Verbier".
- **Which bulletin.** `RegionDayRating.source_bulletin` — the same issue
  the map shows for that day.
- **Modes.** `NEW_BULLETIN` restates the establishing issue every day;
  `CHANGES` sends only when the state moved from the day before. A changed
  update to a day already told sends in either mode. A reissue a reader
  could not tell apart (same fingerprint) never sends.
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

Deferred: weather in the alert (A6), trip alerts (Phase B), the NEW tag on
cards.
