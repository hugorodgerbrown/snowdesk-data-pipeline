---
name: a-resort-page-answers-the-query
description: /resorts/<slug>/ (resort_detail) shows today's danger from the bulletin's own render model, read-only; amends two-documents-and-a-map
status: current
last-reviewed: 2026-10-19
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
  Nothing about the day is stored on, or derived for, the resort.
- **No bulletin today means no danger.** Out of season, or before the day's
  bulletin is ingested, the page says "No bulletin available" and links to
  the bulletin. A past bulletin is never shown as today's.

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
