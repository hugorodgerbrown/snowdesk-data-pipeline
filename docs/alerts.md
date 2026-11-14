---
name: alerts
description: Bulletin alerts — send_bulletin_alerts, BulletinAlert, notify_when, trip_places, the Today page /today/, today_target, bulletin_alerts flag
status: current
last-reviewed: 2026-11-14
---

# Bulletin alerts

One message per person per run, naming every place whose bulletin is new
or has changed. Push first; email when no device can show a notification,
or always if the person asks. Phase A of the roadmap (SNOW-937).

**Going public (SNOW-1361).** The settings, the region-panel prompt and the
since strip stay behind the `bulletin_alerts` flag, which is widened in the
admin rather than in code. The scheduled sender is behind the
`schedule_send_bulletin_alerts` switch. "Going public" records the order.

## The pieces

| Step | What | Code |
|------|------|------|
| State | What a reader would notice in a bulletin: level, subdivision, problems with aspects, elevation band and rating. Version-stamped JSON and a 16-char fingerprint, computed once by the service and stored in the render model's `derived.state` (SNOW-1341) | `apps/ingest/normalise/bulletin_state.py` — `BulletinState`, `bulletin_state`; `state_of` (the stored state, `apps.ingest.public`); `apps/bulletins/services/bulletin_change.py` re-exports them and holds `summarise_change` |
| Decision | Whether a (place, day) sends, is skipped, or is recorded silently | `apps/bulletins/services/alerts.py` — `decide` (pure) |
| Words | One title and body for every alert: a bulletin is out, from whom, when | `apps/bulletins/services/alert_wording.py` — `alert_message` |
| Run | Per account: evaluate every place, one message, deliver, log | `alerts.evaluate_account`, `alerts.deliver`; `send_bulletin_alerts` |
| Log | One `BulletinAlert` row per (account, region, day, state). The previous row is the "last told" state, so the log *is* the change memory | `apps/bulletins/models.py::BulletinAlert` |
| Email | Sent from `ALERTS_FROM_EMAIL` via a django-tasks worker, with one-click `List-Unsubscribe` | `apps/accounts/services/alert_email.py` |
| Landing | Every alert lands on the Today page, `/today/`: a card per place, A–Z, with the detail; tomorrow once any followed place has it (SNOW-1497). `/today/<day>/` takes that day as today; `/alerts/<day>/` redirects there | `apps/public/alert_views.py` (`today`, `alerts_day`), `alerts.today_target`, `public/today.html` |
| Since strip | "Since Friday" sentences at the top of the region body, same words as the alert | `templates/includes/_since_strip.html`, `public.bulletin_panels._since_strip` |
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
  one region are one place and one send-log row; on the Today page each
  name is its own card. The Today page lists every region pin and saved
  resort, switch on or off (`account_places(alerts_only=False)`).
- **Saved trips (SNOW-947, SNOW-945).** A trip the account has saved
  (organiser or not, `Trip.objects.for_user`) is a place on its own day
  only, covering every region its route crosses — `track_regions`, the
  rule the trip page and the Plan page use, so the alert covers what the
  trip page shows. `trip_places` builds them and `places_on` folds them
  into the day's pins. A trip that stays in one region is a place in that
  region, and in a pinned region it is one place and one row, with both
  names. A trip crossing several regions is one place of its own
  (`AlertPlace.regions`, keyed by the trip page): it writes one send-log
  row per region and fires when any of them does. On the Today page it
  is a card per region, each opening the trip page. A track outside every region
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
- **Cold (Hugo, 2026-11-12).** Every alert reads the same, whatever
  fired: the title "New avalanche bulletin", the body "Bulletin released
  at 17:00 by SLF." (`release_line`, the bulletins' `source` and
  `issued_at` in Europe/Zurich). Several providers are joined in a fixed
  order (SLF, ALBINA, Météo-France); bulletins released at different
  times drop the time ("Bulletins released by SLF and ALBINA."). A
  reissue reads the same with its own time. No place, level or count is
  named. The email carries the same words. The earlier detailed wordings
  (SNOW-1332's open door, SNOW-1356's level and move, the "Bulletins for
  your N places" bundle) said too little to act on and too much to
  ignore; the bundle also miscounted a region followed under two names.
- **The landing page.** Every alert opens the Today page, `/today/`
  (SNOW-1497; `/alerts/<latest day>/` before it), which applies the alert's
  own rule — the next day once any followed place has its bulletin — and
  holds the detail and is not clever: one card per name the person
  follows a place by, A–Z, nothing merged or summarised. A card shows the
  danger tile, the level and its move since the day before
  (`alert_wording.trend`: "up from Moderate", "up from 2−", "no change"),
  each problem with its aspect rose and band as the bulletin page lists it
  (`problem_cards_for_bulletin`, so a split day keeps each period apart),
  the forecast new snow
  ("20 cm new snow forecast." at 5 cm or more, `alerts.forecast_new_snow`
  and `NEW_SNOW_MIN_CM`, SNOW-944), the provider with its release and
  validity times, and links to the map (or that name's own trip,
  `AlertPlace.label_urls`) and the provider's PDF. A trip crossing several regions is a card per region. The full
  sentences (`change_sentences`) stay for the since strip. A season start
  or a restart inside the season (`had_bulletin_in_season`,
  `SEASON_GAP_DAYS`) is still decided and logged, but no longer worded.
- **One day per place.** If today and tomorrow both fire for one place,
  tomorrow is the message and today is logged `SKIPPED`.
- **Night hold.** 22:00–06:30 Europe/Zurich nothing is sent; the 06:30
  run picks up what was held. A held run tallies `held`.
- **Gate (SNOW-1491).** A run looks back to the start of the last
  committed run that evaluated (`last_evaluated_at`; held, gated, dry,
  failed and partial runs never count) and evaluates only the accounts
  `accounts_to_evaluate` names since then, less a 15-minute overlap. Four
  things reopen an account: a bulletin changed in a region one of its
  places covers (a `Bulletin` linked to the region written since, which
  survives a deleted `RegionDayRating`, or a rating for today or tomorrow
  written since, or a day the last run did not look at); an undelivered
  alert for today or tomorrow (`FAILED`, or `PENDING` past
  `STALE_PENDING`); a favourite saved or changed, a trip joined, or a
  trip it is on edited; alerts turned on (`alerts_enabled_at`, never a
  profile save). With none the run prints "Nothing changed since …:
  skipped." and tallies `gated`. With no evaluating run yet, or
  `--full`, every alerting account is evaluated
  ([why](decisions/the-alert-run-is-gated-on-change.md)).
- **Reach.** `deliver` writes the rows `PENDING` and queues one worker
  (`_worker_deliver_alert`). It pushes to every active `PushSubscription`
  first, then emails from what actually happened: `ALWAYS` emails anyway,
  `FALLBACK` emails when no push was trusted (no device, every one
  refused — an expired device's 410 included — or every device that took
  one still owes a receipt for its last alert, SNOW-1494: see
  [`push-notifications.md`](push-notifications.md#delivery-receipts-and-retirement)),
  `NEVER` never does, and
  never to an unverified account. The rows become `SENT`, or `FAILED` when
  nothing reached the person. A `FAILED` row is not counted as told, so the
  next run sends again. With no possible channel at all the rows are
  logged `SKIPPED`, so the state still advances.
- **Urgency and lifetime** (SNOW-1495). Each push's `Urgency`, TTL and
  `Topic` follow what the alert says: see
  [`push-notifications.md`](push-notifications.md#urgency-ttl-and-topic).
- **Retry and overlap** (SNOW-1362). A device or the mail server raising
  counts as that channel failing, and a crashed worker still settles its
  rows from what had gone. A `PENDING` row older than `STALE_PENDING`
  (30 minutes) is no longer counted as told, so a lost worker's alert is
  sent again; one a run holds fresh `PENDING` is not claimed by another,
  so two overlapping runs send it once, and a partly claimed bundle sends
  only the places this run won.

## Resending an alert

`resend_bulletin_alert --email <address> --commit` sends an account's last
delivered message again, by its own channels or by `--channel`, without
touching the send log (SNOW-1493). It sends today's wording, from
`alert_wording.cold_copy` over the message's bulletins, not the text stored
when the alert first went (SNOW-1496), and keeps the stored landing url. It runs from the ops tools, so an
operator can test delivery or make up for an alert that never showed. See
[`management-commands.md`](management-commands.md).

## Going public

The record of SNOW-1361, in order:

1. **Email (12 Nov 2026).** Hugo confirmed the provider behind
   `EMAIL_HOST` and SPF, DKIM and DMARC for the `ALERTS_FROM_EMAIL`
   sender. His condition for the first live sends: he is the only account
   on production with alerts on.
2. **Copy (the SNOW-1361 PR).** The help page's accounts topic says
   alerts exist, what they send and links `/account/settings/`; the
   `/compare/` alerts row is `YES` (SNOW-943's change, folded in) and the
   comparison no longer lists alerting as a weakness.
   `tests/public/test_no_bulletin_alert_claims.py` flipped from "no
   promise of alerts" to "no denial of alerts", and scans
   `apps/public/competitor_matrix.py` too.
3. **Ops, on production after the PR ships** (Hugo):
   read a replayed season at `/_alerts-replay/` in both modes; widen
   `bulletin_alerts` in the admin (default: 10% for two days, then
   everyone); then turn on `schedule_send_bulletin_alerts`.
4. **Later.** Remove the flag gate from the code once it has held at 100%
   for a week. `sync_waffle_flags` never re-scopes an existing flag, so the
   manifest's `superusers: true` stays until then; only its note changed.

Deferred: the NEW tag on
cards.
