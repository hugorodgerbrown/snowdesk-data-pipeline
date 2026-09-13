---
name: roadmap
description: Alerting roadmap (SNOW-937) — phase A change-driven bulletin push on region pins, phase B trip-aware alerts, and the season evidence
status: current
last-reviewed: 2026-09-13
---

# Alerting roadmap

Agreed 2026-09-13. The mission reading that drives it: *the bulletin is the
authority, and you should know what it says for where you are going* — not
merely that Snowdesk will show you if you go and look. See
[`definition.md`](definition.md).

**Deadline.** New bulletins begin in November 2026. Phase A ships before
then; nothing in it can be validated against live provider data until the
season opens, so it is built against `seed_test_week` / the golden week
fixture and dry-run in production in November.

## What a bulletin change actually is

Everything below rests on one season of real data rather than on what a
notification *ought* to say. CH-4115 (Martigny / Verbier), 2025-11-01 to
2026-05-18, every SLF issue: 296 bulletins over 189 days, from
`apps/bulletins/local_mirrors/slf_archive.ndjson`. Full write-up with the
season strip and every chart:
[What Changes in a Bulletin](https://claude.ai/code/artifact/7d4a2d15-4707-4422-844e-4da5fc78a04e).

| Across 188 day-to-day transitions | Days | Share |
|---|---:|---:|
| Rating held, a problem or its geometry moved | 107 | 56.9% |
| Nothing changed at all | 55 | 29.3% |
| Headline danger rating moved | 26 | 13.8% |

**The headline rating moves on one day in seven.** An alert keyed to it is
silent on 86% of days and wrong to be silent on two-thirds of those. What
moves instead, on the 107 rating-held days: a persisting problem's elevation
band (56), its aspect sector (48), wind slab arriving (21) or clearing (17),
the problem's own sub-rating (7). Persistent weak layers were named on 166 of
189 days — present 88% of the season, so neither its presence nor its
persistence is news.

**The second daily issue is redundant.** 107 days carried two issues, an
evening bulletin at 15:00–17:00 UTC and a morning update at 06:00–07:00. On
**93 of those 107** the morning update was identical to the evening one on
rating, sub-rating, problem set and problem geometry. Exactly one bulletin all
season carried `unscheduled`.

**Quiet runs are short.** The 55 no-change days fall into 35 runs; the longest
is four days.

**No seasonality.** The rating-move rate sits between 10% and 22% in every
month. The two highest are the shoulders, November (21%) and May (22%).

Three limits, stated plainly: one region, one provider (the Météo-France and
ALBINA archives are committed alongside and unchecked), and one winter —
2025/26 was a persistent-weak-layer season, which is why that problem
dominates.

## What already exists

Verified against the code on 2026-09-13, because the size of both phases
turns on it:

| Piece | Where | State |
|---|---|---|
| Push delivery | `apps/accounts/push_service.py` | Complete — VAPID, Declarative Web Push for iOS 18.4+, 404 hard-delete / 410 soft-delete, `pwa.push.*` telemetry |
| Push async entry point | `enqueue_push` → django-tasks | Complete. One caller: `push_views.push_test` |
| Audience list | `Favourite.objects.region_pins()` | Complete — SNOW-802 turned the old `Subscription` rows into these |
| Per-day rating | `RegionDayRating` | Complete — `min/max/am/pm_rating`, `bands`, `source_bulletin`, and a `version` integer that already increments on rebuild |
| Re-issue signal | `Bulletin.unscheduled`, `issued_at`, `next_update` | Fields populated |
| Ingest cadence | `schedule.py` `fetch_bulletins` | Hourly at `:00,:05` UTC |
| Region → weather | `MicroRegion.centroid_location` → `Weather` | Complete |
| Point → region | `regions/services/point_match.py::region_for_point` | Complete — pure-Python ray-casting |
| Route geometry | `Trip.points` / `Trip.bounds` / `Trip.date` | Complete — snapshot at creation |

So phase A adds a diff, a payload and a dispatcher on top of finished
plumbing. Phase B adds a capability the codebase does not have.

## Phase A — the bulletin notification

Tracked under [SNOW-937](https://linear.app/hugorodgerbrown/issue/SNOW-937/alerting-tell-people-when-the-bulletin-for-a-place-they-follow-changes).
The alert is about a *place*: the audience is `Favourite.objects.region_pins()`.

**Shape.** One notification per meaningful change, carrying what moved:

```
Bulletin update for CH-4115, 12 Nov
Risk increased — Moderate → Considerable · 20 cm new snow forecast
```
```
Bulletin update for CH-4115, 12 Nov
Wind slab now north through east above 2400 · Considerable, unchanged
```

The second of those is the common case, not the exception — it describes 107
days of the season against the first one's 26.

**It fires on change, not on a clock.** The original sketch here was a daily
cron at a fixed local hour; the data killed it. A single daily send misses the
morning update on the 57% of days that carry one, and a fixed twice-daily send
is byte-identical noise on 87% of the mornings it fires. Running just behind
the hourly `fetch_bulletins` and sending only when the authoritative bulletin
for the target day has actually changed gives roughly one send a day, catches
the 14 genuinely-updated mornings, and picks up an unscheduled re-issue with no
special case.

| | Ticket | Size | State |
|---|---|---|---|
| A1 | [SNOW-938](https://linear.app/hugorodgerbrown/issue/SNOW-938) — bulletin change service | M | Ready for dev |
| A2 | [SNOW-939](https://linear.app/hugorodgerbrown/issue/SNOW-939) — delivery preference on the account | S | Ready for dev |
| A3 | [SNOW-941](https://linear.app/hugorodgerbrown/issue/SNOW-941) — notification payload builder | M | Ready for dev |
| A4 | [SNOW-942](https://linear.app/hugorodgerbrown/issue/SNOW-942) — `send_bulletin_alerts`, dispatch on change | M | Ready for dev |
| A5 | [SNOW-943](https://linear.app/hugorodgerbrown/issue/SNOW-943) — flip the `/compare/` alerts row | XS | Ready for dev |
| A6 | [SNOW-944](https://linear.app/hugorodgerbrown/issue/SNOW-944) — weather in the payload | S | Ready for dev |

A4 is blocked by A1–A3 and blocks A5. A6 is additive and the first thing to cut
if November gets close.

**Two decisions the data made, recorded so they are not relitigated:**

- *A1 diffs problems, not ratings.* And it cannot source that from
  `RegionDayRating` — `version` holds `DAY_RATING_VERSION`, an algorithm
  version bumped on rebuild rather than a revision counter, and `bands` is
  ALBINA-only and `None` for every SLF row. Problems live in
  `Bulletin.render_model["traits"][*]["problems"]`.
- *A2 defaults to "only when something changes".* The argument for a daily
  heartbeat was that people forget a channel that goes quiet. The longest
  silence a change-driven feed would have produced all season is four days,
  which does not reach that — and Web Push permission, once revoked, cannot be
  asked for again.

### Phase A risks

- **No live data until November.** Build against the golden week
  (`apps/bulletins/services/golden_week.py`, `seed_test_week`) and the
  committed season archive; the first real run is a `--commit`-less dry run in
  production.
- **Fan-out.** One task per (subscription × pin) per send. `db_worker` on
  production is one dyno; staging has none and runs `ImmediateBackend`, so a
  staging test sends inline and will not surface a queue problem.
- **The season opens quiet.** The first sends land in November when ratings are
  sparse and many regions carry no bulletin at all. `NO_RATING` needs a
  deliberate payload, not a silent skip.

## Phase B — the trip-aware alert

Keyed on a trip rather than a place: *your Saturday on the Rosablanche is
now Considerable, wind slab north through east above 2400*. This is the
`/compare/` row "Rates a specific tour", currently `NO` for us and `YES`
for both WhiteRisk and Skitourenguru — the largest capability gap in the
matrix.

**B1 — route → regions.** Resolve a `Route`/`Trip` `points` snapshot to the
set of `MicroRegion`s it crosses, via `region_for_point`. Store the set so
the alert query is a join rather than a geometry walk. *Size: M.*

**B2 — terrain intersection.** Intersect the bulletin's avalanche problems
— aspect sectors and elevation bands — with the terrain the track actually
crosses. Elevation comes from the GPX; **aspect does not**, and deriving it
needs slope direction from a DEM. This is the real work of phase B and the
only item here that is not an assembly of existing parts. *Size: L, and
that estimate is itself unverified — it needs its own spike.*

**B3 — trip-keyed alerts.** Fire at D-2 and D-1 for a trip's own date,
reusing A2's preference and A3's builder. *Size: M.*

**B4 — `/compare/` row.** As A5. *Size: XS.*

| | Ticket | Size | State |
|---|---|---|---|
| B1 | [SNOW-945](https://linear.app/hugorodgerbrown/issue/SNOW-945) — resolve a route to the regions it crosses | M | Ready for dev |
| B2 | [SNOW-946](https://linear.app/hugorodgerbrown/issue/SNOW-946) — intersect a track with problem geometry | L | Todo — needs a spike |
| B3 | [SNOW-947](https://linear.app/hugorodgerbrown/issue/SNOW-947) — alert on a trip's own day | M | Todo — blocked on B2 |
| B4 | [SNOW-948](https://linear.app/hugorodgerbrown/issue/SNOW-948) — flip the `/compare/` tour-rating row | XS | Todo — blocked on B2 |

**Sequencing.** B1 is useful on its own — it puts a region, and therefore a
bulletin, on a trip page, which is a visible feature without any of B2.
Worth shipping ahead of B2 rather than behind it.
