---
name: resort-page-shows-location-forecasts
description: Resort page renders one labelled forecast per linked Location, and feeds the hero band from the primary location's day-0 row
status: current
last-reviewed: 2026-08-23
---

# Resort page shows one forecast per location — supersedes resort-page-shows-point-forecast

**Supersedes
[`resort-page-shows-point-forecast`](resort-page-shows-point-forecast.md)**
(SNOW-572), which itself superseded
`resort-page-weather-shows-region-snapshot` (SNOW-509).

## Decision

The public resort page renders **one forecast per linked `Location`**, each
labelled with its name and elevation — "Verbier village · 1436 m", "Mont
Fort · 3328 m" — with the resort's primary location leading, then the rest
by ascending elevation.

The **hero band is fed from the primary location's day-0
`ForecastPointWeather` row**, not from the parent region's
`WeatherSnapshot`. The snapshot remains the fallback for a resort with no
linked location.

A resort with exactly one linked location renders what it rendered before
this decision, plus the label it was always missing.

## Why the previous decision had to go

Not because it was wrong when written — SNOW-572's reasoning about reusing
the `_forecast_panel.html` partial and never regressing a resort to no
weather is carried forward intact. It is superseded on one specific point.

**It stacked region-centroid numbers directly above point numbers for the
same day.** The region `WeatherSnapshot` was the unconditional header; the
resort's own point forecast sat underneath. A visitor read two different
temperatures for the same afternoon, from two different places, with
nothing on the page saying either of those things.

[`location-is-the-primitive`](location-is-the-primitive.md) settles the
rule this breaks: **numbers from points, decoration from snapshots.** A
region centroid represents the region and sits at whatever elevation the
polygon's middle falls at; it is not a forecast for the resort and must not
be presented as the resort's headline figure.

**And the single panel was unlabelled.** A resort's stored coordinate is
the geocoder's answer for its *name*, so in practice it is the village:
Verbier's point reads 1436 m against terrain running to 3330 m. That figure
is the right thing to lead with — it is where someone arrives — but the
page showed it as "the resort's weather", with no elevation on it and
nothing higher beside it. On a page people read before going into avalanche
terrain, an unlabelled elevation is the wrong kind of ambiguity.

## Why the region snapshot stays as a fallback

The estate is curated incrementally
([SNOW-701](../management-commands.md)), so most resorts will have no
linked location for a while. Falling back to the snapshot means curation
can land a few resorts at a time without any resort losing weather in the
meantime — the same "coverage gaps are invisible to the visitor" property
the superseded decision valued, reached the other way round.

The same reasoning keeps the **legacy single panel** for an uncurated
resort that still has a `Resort.forecast_point`. The view sets it only when
there are no per-location forecasts, so the two sections are mutually
exclusive, and it disappears with the FK in
[`SNOW-703`](../management-commands.md).

## Consequences

- **Two queries regardless of how many locations a resort has**: one for
  the links joined to their locations, one bulk fetch of the forward window
  for every cell at once, grouped in Python. A per-location query would put
  an unbounded N+1 on a public page — the count is curator-controlled, not
  bounded by the schema.
- **A location with no forecast cell, or a cell with no rows, is omitted**
  rather than rendered empty. No empty state: a labelled section promising
  weather it cannot show is worse than no section.
- **The hero takes only a day-0 row that is actually today.** A cell whose
  stored window happens to start tomorrow hands the hero nothing and it
  falls back to the snapshot, rather than captioning tomorrow's figure as
  today's.
- **Ordering is primary-first then ascending elevation**, which reads as
  the way up the mountain. `is_primary` normally marks the `BASE` link,
  preserving "the page leads with the village" — which is where someone
  arrives, and remains the right hero.
- The `?variant=panel` HTMX retry for the region panel is untouched and
  still applies to the fallback path.
- Mont Fort being one row shared by four resorts means the four render the
  *same* panel object from the same cell — the sharing
  [`location-is-the-primitive`](location-is-the-primitive.md) exists for,
  visible on the page rather than only in the schema.
