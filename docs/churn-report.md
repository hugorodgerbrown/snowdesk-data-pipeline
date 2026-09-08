---
name: churn-report
description: Weekly churn chart — bin/render-churn, the report-churn skill, the Snowdesk Churn Ledger artifact, what is excluded from churn and why
status: current
last-reviewed: 2026-09-08
---

# Churn report

A chart of the repository's own history: commits merged, releases cut, and
lines added and removed, per ISO week, since the first commit. It answers
whether the project's output is speeding up, slowing down, or turning over —
a question git can answer and nothing else in the repo asks.

## The three pieces

| Piece | What it is |
|---|---|
| [`bin/render-churn`](../bin/render-churn) | Reads git, aggregates per ISO week, emits a self-contained HTML fragment. Read-only; stdout unless `--output`. |
| [`.claude/skills/report-churn/`](../.claude/skills/report-churn/SKILL.md) | Runs the renderer and republishes the artifact. Supports unattended Routine mode. |
| **Snowdesk Churn Ledger** | The published artifact — one URL, republished in place. The skill holds the address. |

```bash
uv run python bin/render-churn --output /tmp/churn.html
```

A weekly Routine invokes the skill; the chart is otherwise refreshed on
request. Nothing regenerates it on deploy, and nothing needs to.

## Nothing on the page is authored

Every figure — the totals, the axis maxima, the peak weeks, the prose in
"How this was counted" — is computed from `git log` at render time. This is
deliberate and it is the whole design: a number typed into the template is a
number that goes wrong on the next merge without announcing itself, which is
the same reasoning [`bin/render-day-summary-matrix`](../bin/render-day-summary-matrix)
gives for computing its census rather than carrying it as prose.

So **the rendered HTML is not committed.** It is a projection of the history;
re-running the script is how it is kept current, and any past state is
recoverable by rendering from an older ref. `docs/page-audits/` commits dated
files for the opposite reason — an audit is judgement that cannot be
regenerated.

## What counts as churn

Churn is `git log --numstat` over **first-parent** history. Every commit on
`main` is a squash merge, so one commit is one merged pull request and nothing
is double-counted.

These paths are counted separately as **data churn**, and kept off the chart:

- `fixtures/` (any app)
- `sample_data/`
- `uv.lock`, `package-lock.json`, `poetry.lock`
- `.po` / `.mo` message catalogues
- `static/css/output.css`
- `.ndjson` archives
- `docs/archive_pdfs/`

The exclusion is load-bearing rather than tidy-minded. PR #153 imported the
EAWS micro-region polygons for Austria and Italy: ~570,000 lines in two
fixture files, in one week. Counted as churn it is six times the largest real
week in the history, and every other week on the chart reads as flat against
it. The lines are still reported — in the table's last column — because a
fixture import is a real event, just not a measure of work done.

The pattern lives in `GENERATED` in the renderer, and
`tests/bin/test_render_churn.py` asserts both halves: that every documented
exclusion matches, and that ordinary source paths do not.

## Two things the chart deliberately does not do

- **Weeks that merged nothing are drawn as zero bars, not dropped.** Two
  exist. A gap in output is part of the picture; an axis that silently closes
  over it is not.
- **Releases before the `main` → `release` split read as zero**, because
  there was no release branch then. The page says so rather than leaving the
  reader to infer inactivity.

## Related

- [`docs/deployment.md`](deployment.md) — the release tags the middle panel counts.
- [`.claude/README.md`](../.claude/README.md) — how skills and Routines fit together.
