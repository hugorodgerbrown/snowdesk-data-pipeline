---
name: a-resort-page-answers-the-query
description: /resorts/<slug>/ (resort_detail) shows today's danger read-only, plus one band-vs-lift-range line per problem (_problem_band_relation)
status: current
last-reviewed: 2026-10-20
---

# A resort page answers the query

Amends [`two-documents-and-a-map`](two-documents-and-a-map.md) for resort
pages only (SNOW-1124).

## Decision

`/resorts/<slug>/` shows **today's avalanche danger**: the level, the
bulletin headline and the avalanche problem cards, with their aspects,
elevations and full prose. It is no longer only a router to the two
documents.

The guard that keeps it from becoming a third document:

- **The bulletin stays the one full document.** The resort page shows the
  danger and the problems, not the snowpack, weather, outlook or metadata,
  and its danger area is one link, "Full bulletin →", to the region's
  evergreen bulletin.
- **The same render model, read-only, never a copy.** `resort_detail`
  selects the issue exactly as the bulletin page does —
  `issues_for_date(region, today)`, then `_resolve_selected_issue` (the
  10:00 rule), then `_build_panel_context` — and words the headline with
  the bulletin page's own helper, `_build_bulletin_headline`. The cards
  render through `public/_rating_block.html`, the bulletin page's partial.
  Nothing about the day is stored on the resort, and the one thing derived
  for it is the numeric line below.
- **No bulletin today means no danger.** Out of season, or before the day's
  bulletin is ingested, the page says "No bulletin available" and links to
  the bulletin. A past bulletin is never shown as today's.

## One derived claim per problem (SNOW-1143)

Under each problem card the page adds **one line** saying how the
problem's elevation band sits against the resort's lift range
(`Resort.base_elevation_m`..`Resort.top_elevation_m`) — "Within Verbier's
lift range", "Above 2200 m: covers the upper part of Verbier", "Above
Verbier's top lift (3330 m)" and so on. It is the only thing the page
derives for the resort, and it is a **straight numeric comparison**: two
ranges against each other, nothing about piste, off-piste or terrain.
`_problem_band_relation` in `apps/public/views.py` holds the rule and its
edge semantics (a bound equal to the base or top counts as reaching it).

Why: the reader landed on "Verbier avalanche" to learn whether today's
problems are where they will ski. The bulletin answers in metres; the page
already holds the resort's metres; putting the two side by side is
arithmetic the reader would otherwise do in their head. It stays a
numeric claim so it can never disagree with the bulletin it reads.

The rules that keep it honest:

- **A treeline bound gets no verdict.** The treeline has no number, so the
  line states the band and the lift range side by side ("Above the
  treeline. Verbier's lifts run 821–3330 m") and lets the reader judge.
- **No elevation means all elevations** ("All elevations, including all of
  Verbier") — except on a card showing "See description below", where the
  scope is in the prose and an "all elevations" claim could contradict it;
  that card gets no line.
- **No lift range, no line.** A resort missing either elevation (or whose
  top is not above its base) shows the cards alone.
- One disclaimer under the heading, only when a line renders: "Compared
  with {resort}'s lift range ({base}–{top} m). Off-piste terrain may
  differ." The bulletin page and `_rating_block.html` are untouched; the
  line renders after the partial, outside `[data-testid="resort-danger"]`.

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
- `day_rating` (`RegionDayRating`) stays in the resort context only for
  the meta description (SNOW-1122).
- The reused partials can carry links (problem prose, field notes), so the
  problem cards render outside `[data-testid="resort-danger"]`, which must
  hold exactly one `<a>` — `tests/e2e/test_resort_page.py` clicks it.
- The rest of `two-documents-and-a-map` stands: the bulletin and the
  weather page are the two documents, and nothing else gains a page from
  this amendment.
