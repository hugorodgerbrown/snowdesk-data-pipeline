# Bulletin change frequency

Evidence behind the alerting scope in
[SNOW-937](https://linear.app/hugorodgerbrown/issue/SNOW-937), and behind
SNOW-938's design.

Most recent: [2026-09-13](2026-09-13.html) — *What Changes in a Bulletin*.

## The question

Snowdesk is going to notify people when the bulletin for a place they follow
changes. Before the notification is written, what is a bulletin change? If it
is a danger-rating move, how often does the rating actually move, and what is
happening on the days it doesn't?

## Method

One region, one full season, every issue: **CH-4115 Martigny / Verbier,
2025-11-01 → 2026-05-18, 296 SLF bulletins over 189 days**. Where a day
carried more than one issue, the latest-published is the one compared, except
in the second-issue section, which compares each day's earliest and latest
issue directly.

A day is classed by comparing it with the day before on danger rating,
sub-rating, problem set and each problem's elevation band and aspect sector.
"Detail moved" means a problem was added or removed, or a problem that
persisted moved its band, its aspects, or its own sub-rating.

The season carries **one coverage gap** — no bulletin was issued between 6 and
15 November — so the 189 days yield **187** transitions, not 188: the pair
either side of the gap is eleven days apart and is not a day-to-day
comparison.

## What it found

- **The rating moves on 13.4% of days** — 25 of 187 transitions. Every move
  was a single step, 13 up and 12 down, with no two-level jump all season.
- **On 57.2% the rating held and something else moved**, so a notification
  keyed to the rating is silent on 87% of days and wrong on two-thirds of
  those. Only 29.4% of days are genuinely unchanged.
- **Geometry is the commonest event**: 56 elevation-band moves and 48 aspect
  changes on the 107 rating-held days — the kind of change that decides
  whether a planned route is affected, and one the danger scale cannot say.
- **Wind slab is the volatile problem** (38 of 68 arrivals and departures),
  while persistent weak layers were named on 166 of 189 days and so carry
  almost no notification value.
- **The second issue usually says nothing new.** 107 days carried two issues;
  the morning update was identical to the evening bulletin on 93 of them. A
  fixed twice-daily send is noise 87% of the time it fires.
- **There is no quiet season.** The rating-move rate stays between 10% and 22%
  in every month, the 55 no-change days fall into 35 separate runs, and the
  longest quiet run all season is four days — the one real silence being the
  November coverage gap, when there was no bulletin to send.

## Format

A self-contained HTML file (`YYYY-MM-DD.html`), same convention as
[`docs/page-audits/`](../../page-audits/README.md): no `<!doctype>`, `<html>`,
`<head>` or `<body>` tags of its own, so it publishes as an Artifact and still
renders opened straight from disk. The season strip carries one column per day
with the rating in the EAWS scale's own colours, and each column's tooltip data
is embedded in the file — there is no data fetch and no external dependency
beyond the Google Fonts stylesheet.
