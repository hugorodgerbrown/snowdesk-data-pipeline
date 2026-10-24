---
name: a-resort-page-answers-the-query
description: resort_detail: forecast first, then the day's danger card (day_windows rows), then the problem cards; /resorts/<slug>/<date>/
status: current
last-reviewed: 2026-10-23
---

# A resort page answers the query

Amends [`two-documents-and-a-map`](two-documents-and-a-map.md) for resort
pages only (SNOW-1124). Narrowed by SNOW-1156: the page is a **landing
page** that answers the query in one card and leads into the bulletin.
Amended by SNOW-1164 ([`the-sheet-is-the-page`](the-sheet-is-the-page.md)):
the page's body is also the map's resort sheet, so it takes a day, and
it shows the day's problems again. Amended by SNOW-1203: the forecast
leads in every season and the card states the level with the region
panel's Day Risk Profile rows — see the last two sections.

## Decision

`/resorts/<slug>/` shows **today's avalanche danger** in one compact card,
`public/partials/_resort_bulletin_card.html`, under the eyebrow "Regional
avalanche bulletin • {today}":

- the level — the EAWS tint band, the `.danger-tile` and the level word —
  and the region;
- the first (dominant) problem's aspect rose and elevation pictogram, when
  it has either;
- the bulletin headline;
- "{provider} · updated HH:MM" — the bulletin's publication time on
  Central European time (`_resort_bulletin_attribution`);
- one link, "Read the full {region} bulletin →", to the region's evergreen
  bulletin.

It is no longer only a router to the two documents, and it does not try to
answer every question either: the detail lives in the bulletin, one tap
away.

The guard that keeps it from becoming a third document:

- **The bulletin stays the one full document.** The resort page shows the
  level, where the main problem sits and the headline — not the problem
  prose, the snowpack, weather, outlook or metadata — and its danger card
  is one link to the region's evergreen bulletin.
- **The same render model, read-only, never a copy.** `resort_detail`
  selects the issue exactly as the bulletin page does —
  `issues_for_date(region, today)`, then `_resolve_selected_issue` (the
  day's latest issue, SNOW-1207), then `_build_panel_context` — and words the headline with
  the bulletin page's own helper, `_build_bulletin_headline`. Nothing about
  the day is stored on the resort, and nothing is derived for it.
- **No bulletin today means no danger.** Out of season, or before the day's
  bulletin is ingested, the card says "No bulletin today", names the
  resort's typical season, and links to the latest bulletin. A past
  bulletin is never shown as today's.
- **The season decides the order.** With a bulletin today the card leads
  the main column; without one the forecasts lead and the card follows
  them, because out of season there is no danger to lead with.

## The problem cards and the lift-range line, retired (SNOW-1156)

SNOW-1124 put the bulletin page's own problem cards on the resort page
(`public/_rating_block.html`, with aspects, elevations and full prose), and
SNOW-1143 added under each one a line comparing its elevation band with the
resort's lift range (`_problem_band_relation`, "Above 2200 m: covers the
upper part of Verbier") with a disclaimer under the heading. SNOW-1156
removed both, on Hugo's decision, with `_resort_problem_rows`,
`_problem_band_relation`, `_numeric_band_relation` and their tests.

Why: the page is the landing page for "{resort} avalanche", and its job is
to answer "how dangerous is it today" and lead the reader into the
bulletin, not to be a second reading of it. With the full problem cards
the resort page repeated most of the bulletin — the very drift into a third
document the guard above exists to prevent — and pushed the forecasts and
the season below a long column. The lift-range line was the one claim the
page derived for itself; useful arithmetic, but a claim the bulletin does
not make, beside a disclaimer that it might not hold off-piste. One card
showing the level, the dominant problem's aspects and elevation, and the
headline answers the query at a glance; the bulletin answers the rest.

## Why

The page survived SNOW-807 as a landing page for a search term: people
search "Verbier avalanche", not "CH-4115". But a page that only links
onward cannot rank for "{resort} avalanche" — it holds nothing about the
avalanche, so a search engine has no reason to send the query there, and a
reader who lands on it has to tap through to learn the one thing they came
for. Answering the query on the page is what makes the landing page work.

Reading the render model, rather than `RegionDayRating` or a resort-side
summary, keeps the two pages from disagreeing: they show the same issue,
the same level and the same words.

## Consequences

- A change to the bulletin page's issue selection or headline inputs
  reaches the resort page too; that is the point. Change
  `_build_bulletin_headline`, not a copy of it.
- The meta description's danger sentence reads the same `panel` as the
  card (SNOW-1122), so the two can never name different levels.
- `[data-testid="resort-danger"]` is the card and must hold exactly one
  `<a>` — `tests/e2e/test_resort_page.py` clicks it. Nothing that can carry
  its own link (problem prose, field notes) is drawn inside it.
- The rest of `two-documents-and-a-map` stands: the bulletin and the
  weather page are the two documents, and nothing else gains a page from
  this amendment.

## The page takes a day, and the problems return (SNOW-1164)

The map's resort sheet now renders this page's body for the map's day, so
the page takes one too: `/resorts/<slug>/<YYYY-MM-DD>/`. The bare URL is
still today, and the rules above read "the day" for "today": the issue is
selected for that day, the card says "No bulletin this day" when none
covers it, and its link goes to that day's bulletin page. Only today's
page is indexed; another day's is `noindex`.

Under the card the body lists the day's problem cards
(`public/_rating_block.html`) as the bulletin gives them. Hugo chose this
on 21 Oct 2026 when the sheet's extras moved into the body. The sheet's
"Above / Below this location" verdict did not come with them: it judged
each band against one height (the pin, normally the village), and on a
resort that spans 1,500–3,330 m it read as a claim about the whole
resort. It reverses the first half of SNOW-1156's retirement; the
lift-range line stays retired, and the card itself is
unchanged and still holds exactly one link.


## Weather first, and the region panel's level row (SNOW-1203)

Hugo, 23 Oct 2026, from a board comparing the resort and region sheets
for the same day:

- **The forecast leads, in every season.** It is the resort's own data;
  the bulletin is the region's. The body is forecast → "{region} avalanche bulletin"
  card → problem cards → season calendar, and "the season decides the
  order" above no longer holds.
- **The card states the level as the region panel does.** It renders
  `includes/day_windows.html` (flush inside the card) from the same
  `_build_day_windows` the bulletin body calls, so a split day shows its
  windows on both surfaces. The card's own EAWS band went, and with it the
  row that copied the *first* problem's aspects and elevation: the problem
  cards directly below already state each problem's scope, so the row said
  it twice, and on a two-problem day it named only one of them.
- **No headline.** The region panel no longer shows it, and on most days
  it was a generic sentence ("Danger level 2 moderate. Read the bulletin
  carefully."). `_build_bulletin_headline` stays the bulletin page's.

The card still holds exactly one link, and the no-bulletin card is
unchanged.
