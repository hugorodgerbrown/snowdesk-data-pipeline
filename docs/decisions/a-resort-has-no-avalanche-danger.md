---
name: a-resort-has-no-avalanche-danger
description: _resort_body.html shows no danger; neutral region row (_place_link_row.html); panel kind line; map_panel_trail.js back pill
status: current
last-reviewed: 2026-10-25
---

# A resort has no avalanche danger of its own

Supersedes [`a-resort-page-answers-the-query`](a-resort-page-answers-the-query.md)
(SNOW-1124, SNOW-1156, SNOW-1203) and amends
[`the-sheet-is-the-page`](the-sheet-is-the-page.md) for resorts. Hugo,
24–25 Oct 2026.

## Decision

**Danger belongs to the region; a resort is a place and its weather.** The
resort body (`_resort_body.html`, on the page and in the map's place sheet)
is the forecast for its Base / Mid / Summit points, the curated facts, and
**one row naming the avalanche region the resort sits in, with no level**
(`includes/_place_link_row.html`). The bulletin card, the day's problem
cards and the season's danger calendar are gone from it. The page's title
and description make no danger claim.

**Every map panel names its kind above its title**: "Avalanche region",
"Ski resort" (or "Saved place"), "Weather point" — the eyebrow, beside the
tile shape the panel already led with (square, round, weather icon).

**Every panel ends with rows to its neighbours**: a region lists its
resorts, a resort names its region, a weather point names the resort it
stands for and its region. On the map a row opens that place's panel in
place (`static/js/map_panel_trail.js`) with a one-step back pill naming
where the reader came from; off the map it is a plain link to the page.

## Why

Hugo compared the three panels side by side (24 Oct): a region, a resort in
it and a weather point named after the region all opened the same frame,
and the resort repeated the region's level, bulletin card and wind-slab
card, so it read as a second copy of the region. The only real difference
was the 46 px tile. A resort spans a range of heights and sits inside a
region's forecast; it does not have a danger level of its own, and showing
one invited the reader to treat it as if it did. Weather is specific to a
place; bulletins are regional — so the place's own data leads, and the
region is one tap away.

The region row carries no level on purpose (option chosen on 24 Oct over a
row with the level chip, and over a bare text link): a level on it would put
danger back on the resort.

## Consequences

- The resort page no longer ranks on "Verbier avalanche danger today" with a
  level in its snippet; it names the region's bulletin it sits under
  instead. That was SNOW-1124's reason for showing the level, and it is
  given up deliberately.
- `_resort_bulletin_card.html`, `_resort_bulletin_attribution`,
  `_resort_bulletin_url` and `_resort_season_calendar_context` are gone;
  `build_resort_body` selects no bulletin, so the page costs fewer queries.
- The trail is one step deep. Following a row replaces it, the pill spends
  it, and once its panel has shown, another place painted into that panel
  or the panel closing ends it. A deeper stack would be a history the
  reader cannot see.
- A weather point that is neither a resort's point nor a region's centroid
  offers no "Where this is" rows rather than scanning every boundary on a
  tap (`views._weather_point_places`).
