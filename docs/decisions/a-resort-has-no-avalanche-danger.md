---
name: a-resort-has-no-avalanche-danger
description: _resort_body.html region row shows the REGION's level (day_rating) or no bulletin; panel kind line; map_panel_trail.js
status: current
last-reviewed: 2026-10-27
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

## Amended 27 Oct 2026 — the callout shows the region's level

Hugo's resort panel review (27 Oct 2026) put the level back on the region
row, as the region's: the row's square is the region's danger tile for the
day (`_place_link_row.html` with `day_rating`, from `RegionDayRating`), its
title the level's name and its subtitle "<region> · View bulletin", under
the eyebrow "Avalanche bulletin • <provider>". With no rated day the row
becomes "No bulletin available on this date." and a "View bulletin
history →" link to the region's page, whose season calendar is that
history. The 24 Oct choice of a level-less row is reversed for this one
row; the rest of this decision stands — no bulletin card, problem cards or
season calendar on the resort, and the level is shown as the region's,
named beside it, never as the resort's.

The same review reshaped the phone sheet around it: the resort sheet opens
at HALF (title, date, this callout and today's weather), the curated "why
it matters" line moved into the folded "About this resort", the forecast's
Base / Mid / Summit chips moved under the week as one row, and the weather
section's eyebrow carries its source and time ("Weather • Open-Meteo
06:01") in place of the footer line.

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
- Moving between panels loads into the same open panel and never zooms
  (Hugo's desktop review, 25 Oct): a resort asked for from another panel
  keeps the reader's zoom and pans only when its pin is off screen or under
  a panel (`panIntoView` in map.js); a region row still frames the region.
  A panel showing a new place, or opening at all, starts at its top; a
  repaint of the same place (a change of day) keeps the reader's place.
- On a phone the next panel opens at the height the reader had: a row or
  the back pill calls `MapSheet.handOff()` while the old sheet still
  rests at its detent, and the next sheet to open takes it instead of its
  initial PEEK (Hugo, 25 Oct: FULL → FULL). The hand-off is taken once
  and lapses after five seconds, and a panel that does not declare that
  detent opens at its own initial one.
- A panel row's click is not a click outside the panel it opens
  (`map_sheet.js`): the region sheet opens a resort inside its own click
  handler, and the new sheet used to read that same click as one outside
  it and close at once. The trail is read in the capture phase for the
  same reason — by the bubble phase the row has been repainted away.
- A weather point that is neither a resort's point nor a region's centroid
  offers no "Where this is" rows rather than scanning every boundary on a
  tap (`views._weather_point_places`).

## The first visit is taught it (SNOW-1231)

Someone arriving for the first time looks for their resort's danger, and
this decision means they will not find it on the resort. So the welcome
card's "Find a resort" is a three-step walkthrough rather than plain search
(`static/js/map_resort_guide.js`, `apps/public/resort_guide.py`): search
opens with a few popular resorts, the resort's sheet says the resort has no
danger level of its own and rings its region row, and the region's sheet is
the bulletin. It watches the ordinary surfaces rather than replacing them,
so what it teaches is exactly what the map does every other day.

- **Popular resorts are a fixed list** (`POPULAR_RESORT_SLUGS`: Verbier,
  Zermatt, Davos, St. Moritz — Hugo, 26 Oct). Every resort is Swiss, so
  placing the visitor by IP would change little and cost a lookup.
- **Out of season it borrows the example route's day** (Hugo, 26 Oct). With
  no bulletin today the lesson would end on an empty page, so it moves the
  map to `RouteShare.example_date` with the "Example day" badge and back to
  today at the end, as the example walkthrough does. In season it keeps the
  map's own day: the live bulletin is what the visitor came for.
- **Pinning needs an account**, as the sheet's star does. The finish card's
  pin is a `[data-resort-star]` that favourites.js saves; signed out it is a
  sign-in link whose `next` reopens the resort on the map.

## The region row leads the resort (user testing, 27 Oct 2026)

User testing found the walkthrough's "Tap it" pointing at a row that sat
under the forecast and six fact rows, and the guide's ring drawn inside a
card that clipped its corners. So, for every reader and not only during the
guide (Hugo, 27 Oct):

- **The region row comes first** in `_resort_body.html`, under the header,
  and reads the region's name over "View region bulletin for <day>". The
  forecast follows it. This reverses SNOW-1203's "the forecast leads"
  ([a-resort-page-answers-the-query](a-resort-page-answers-the-query.md)):
  the bulletin is the reason most people open a resort.
- **In the map the facts are folded** behind a closed "About this resort"
  panel (`includes/_collapsible_panel.html`, `folded=True` on
  `_resort_facts.html`). The resort page keeps them open in its sidebar.
- **The guide rings the card, not the row** (`[data-place-link-card]`), in
  the accent colour with a halo, and says "Avalanche bulletins are
  regional. Tap the highlighted row to view the local conditions."
