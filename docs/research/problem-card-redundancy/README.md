# Problem-card redundancy

Design research behind [SNOW-727](https://linear.app/hugorodgerbrown/issue/SNOW-727/cut-the-redundant-problem-card-chips-and-give-the-eaws-level-colour-a).

Most recent: [2026-08-26](2026-08-26.html) — *Four Ways to Say Dry*, plus a
27 Aug addendum decomposing the 312 split days by direction.

## The question

The bulletin page's problem card names its dry/wet category in the panel
title, encodes it in the EAWS pictogram, implies it in the problem label, and
then states it again in a `DRY` / `WET` pill. The `ALL DAY` pill has the same
problem against the Day Risk Profile row above it. Which of those signals are
load-bearing, and which are decoration?

The 27 Aug addendum asks the next question down. The sweep established that 312
of the bulletins split the day; it never asked which way they go. Grounding the
Day Risk Profile's second row needs that direction, not just the count.

## Method

The counts come from the **production** database (`render_model→traits`), not
the committed mirrors under `apps/bulletins/local_mirrors/`. The distinction is
load-bearing: the raw SLF feed leaves 60% of aggregation titles empty, and
`render_model.py:1604` fills every one of them before a page is rendered.
Reading the mirrors alone understates the duplication by more than half.

Contrast figures are sRGB WCAG 2.1 ratios computed against the shipped token
values in `src/css/main.css`.

The addendum is the one exception, and deliberately so: it is computed from the
committed mirrors under `apps/bulletins/local_mirrors/`. It reads
`dangerRatings[].validTimePeriod`, which `danger.ratings` passes straight
through (`render_model.py:447`) — there is no fallback filling it, so the
objection above does not apply. The corpora differ in size (8,080 mirror
records against 7,855 production bulletins carrying a trait) but return the
same 312 split days, which is the set the addendum divides.

## Format

A self-contained HTML file (`YYYY-MM-DD.html`), same convention as
[`docs/page-audits/`](../../page-audits/README.md): no `<!doctype>`, `<html>`,
`<head>` or `<body>` tags of its own, so it publishes as an Artifact and still
renders opened straight from disk. Fonts are linked from Google Fonts — the
only host an Artifact's CSP admits — with real fallback stacks.

The card specimens are reproduced from Snowdesk's own tokens rather than
approximated, so a treatment that looks wrong on the page looks wrong in the
product. The EAWS pictogram is drawn rather than lifted from
`static/icons/eaws/`; the dry/wet glyph difference it carries is the real one.

## What it concluded

- The category pill is never the only signal — 9,614 of 9,614 traits carry a
  title that names the category.
- The time pill distinguishes nothing on 96.0% of pages.
- The panel title stays in the card. Hoisting it out into a section heading
  was mocked up and rejected.
- The saturated EAWS colour deserves more than the 4px stripe at
  `main.css:623`. Four treatments are compared across all five levels; two
  levels are too pale for a bare border and two are too red for a filled row,
  so neither naive form survives the whole scale.

## What the addendum found

- The two split signals never disagree: 312 bulletins split on both trait
  period and danger-rating period, 7,768 on neither, none on one alone. The
  profile row and the card pills appear and vanish together.
- Of the 312, 189 deteriorate, 101 hold level, 22 improve — deterioration
  outnumbers improvement 8.6:1, and only 10 days fall a whole level.
- Every improving day in the archive is SLF. ALBINA splits 127 times and never
  falls; Météo-France carries no `validTimePeriod` on its ratings, so its 4,671
  bulletins cannot split.
- The flat-but-split case (101) is the design problem: two rows carrying the
  same digit, word and colour, where the news is the problem changing
  underneath them.

Two contrast defects surfaced along the way, both pre-dating the work: the
band's ghost chips fail AA in dark mode, and `.dw-tile` fails AA at levels 4
and 5. Details in the ticket.
