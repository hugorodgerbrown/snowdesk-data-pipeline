# Day-summary matrix

Copy review behind [SNOW-738](https://linear.app/hugorodgerbrown/issue/SNOW-738/make-the-day-summary-describe-the-bulletin-not-the-rule-that-fired).

Most recent: [2026-08-27](2026-08-27.html) — *The day summary, one sentence at
a time*: all 80 cells, grouped by how the day moves, with the archive
frequency of each.

## The question

The one-line summary beside the day-character label used to be one of five
fixed strings, one per cascade branch. "Persistent or gliding-snow problems
can mask the real risk" rendered on every hard-to-read page whether the
problem was persistent weak layers or gliding snow, whether the danger was 2
or 3, and whether it held all day or doubled by mid-afternoon.

Replacing it needs an answer to two questions at once: which combinations of
danger level and problem type a season actually produces, and whether one
sentence per combination reads as English when every combination is set side
by side. A matrix spread across a module answers neither. This page is the
review surface for both.

## Method

Counts come from the **committed season mirrors** under
`apps/bulletins/local_mirrors/` (8,080 bulletins), replaying
`build_render_model` and `compute_period_transition`. That is the same
exception the [problem-card-redundancy](../problem-card-redundancy/README.md)
addendum takes, and for the same reason: `danger.ratings` passes
`validTimePeriod` straight through, so the aggregation-title fallback that
makes the mirrors unsafe for a title census does not apply here. The
movement figures reproduce that addendum's split counts exactly — 189 rising,
101 flat, 22 falling of 312.

A cell the archive has never produced still renders its sentence, against
illustrative problems, and is marked *not seen*. Provider behaviour is not a
contract, and an unfired cell is the one nobody would notice rendering badly.

## Format

Generated, not hand-authored. **Re-run
[`bin/render-day-summary-matrix`](../../../bin/render-day-summary-matrix)
after editing any cell in `apps/bulletins/services/day_summary.py`** — the
page carries no transcribed copy and no transcribed figures, so a stale page
is visibly stale rather than quietly wrong:

```bash
uv run python bin/render-day-summary-matrix --output docs/research/day-summary-matrix/$(date +%F).html
```

A self-contained HTML file (`YYYY-MM-DD.html`), same convention as
[`docs/page-audits/`](../../page-audits/README.md): no `<!doctype>`, `<html>`,
`<head>` or `<body>` tags of its own, so it publishes as an Artifact and still
renders opened straight from disk. Fonts are linked from Google Fonts — the
only host an Artifact's CSP admits — with real fallback stacks. Level swatches
follow the EAWS danger scale, which is the one palette this domain owns.

## What it concluded

- **96.4% of bulletins never change across the day.** The static cells are the
  feature; the changing ones are the tail.
- **A split day is the sun getting to work.** Wet snow is the arriving problem
  on 254 of the 312 split days.
- **Nothing ever improves.** All 22 falling days replace a dry problem with wet
  snow — the number drops while the hazard swaps. The `easing` copy says so
  rather than offering the afternoon as the safer half, and a test asserts
  "improving" cannot appear in it.
- **23 of the 101 flat splits change nothing actionable** — 17 keep the same
  problems on new ground, 6 carry two identical windows. Those classify as
  static, and the Day Risk Profile's caption was re-gated to match.
- **A fifth of changing days move without their digit changing.** Direction
  ranks on `(level, subdivision)`, so naming both ends produced "moderate this
  morning, moderate by afternoon" on 45 pages. Rising and easing cells now open
  on a transition clause with a same-band variant; the page shows both.

The matrix contract, the axes and the editing rules live in
[`docs/day-summary.md`](../../day-summary.md).
