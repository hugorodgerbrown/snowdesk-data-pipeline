# Page audits

Most recent audit: [2026-08-31](2026-08-31.html)

## Purpose

Longitudinal record of the **product** state of every public page: what each
page says, what it does, what it promises that the code doesn't deliver, and
how the pages link to one another.

This is deliberately not a code review. `docs/code-reviews/` tracks drift,
dead code and pattern consistency — implementation quality. A page audit asks
a different question: *is this page finished, is it reachable, and is it
telling the truth?* The two have found disjoint sets of problems. The first
page audit (2026-08-11) surfaced a subscription flow promising a daily email
that nothing sent, three routed pages with zero inbound links, and legal pages
covering one of three data providers — none of which a code review would flag,
because every line of it was well-written.

## Format

Each audit is a **self-contained HTML file** (`YYYY-MM-DD.html`), not
markdown. The format earns its keep: page inventories are tables, the link
graph is a diagram, and finding severity reads better as a visual register
than as prose. The file is written to be published as an Artifact — so it
carries no `<!doctype>`, `<html>`, `<head>` or `<body>` tags of its own — and
still renders correctly opened straight from disk.

Fonts are linked from Google Fonts rather than inlined. The site itself
self-hosts DM Sans and DM Mono (`src/css/main.css`) so no visitor depends on a
third party; that reasoning doesn't apply to an internal artefact, and
inlining them as base64 added 87 KB of undiffable noise to every weekly
commit.

`bin/docs-lint` only scans `*.md`, so the HTML files carry no frontmatter
requirement. This directory is in the linter's `EXCLUDED_DIRS` so that this
README doesn't need frontmatter or a routing-table entry either — matching
`code-reviews`, `qa`, `research` and `screenshots`.

## Cadence

Weekly, via the **"Weekly Snowdesk page audit"** Routine (Mondays, 07:00 UK).
Monday morning rather than the crowded Sunday-evening slot that already holds
the code review, both dependency updates and the competitor scan.

- **Unattended:** the Routine calls `/audit-pages routine`, which runs
  end-to-end with no approval gate — branch → audit → dated doc → README
  pointer → PR.
- **On-demand:** run `/audit-pages` interactively for a fresh read at any
  time.

## What each audit must do

The value of this format is the **delta**, not the restatement. An audit that
re-describes the site from scratch each week is noise; an audit that says what
changed, what closed, and what has now been open for six weeks is a signal.

So every audit after the first:

1. Reads the previous dated file in this directory.
2. Re-verifies each open finding **against the code**, never against ticket
   state. Tickets say what someone intended; only the code says what shipped.
3. Reports closures with evidence, unchanged findings with a re-verification
   note, and anything new.
4. Draws out consequences the tickets can't see from inside themselves —
   a decision that unblocks a parked ticket, or obsoletes one before it is
   built.

## Layout of each audit

Sections in order, omitting any that would be empty:

- **Masthead** — date, baseline commit → current commit, commit/file counts
- **The short version** — what a reader needs if they read nothing else
- **Closed** — findings fixed since the last audit, each with the evidence
  that proves it, and the ticket that did it
- **Unchanged** — a table: finding, re-verified state, ticket
- **New** — work or architecture the previous audit didn't anticipate
- **Consequences** — connections between recent decisions and older tickets
- **Recommendation** — what to do next, ranked, with reasons

## History

| Date | Baseline | Headline |
|------|----------|----------|
| [2026-08-31](2026-08-31.html) | `4fdb414e` → `52c57eae` | Eight findings closed, including both top recommendations — but the Resend email claim survives a legal-pages rewrite built to fix lines around it |
| [2026-08-24](2026-08-24.html) | `407e552` → `4fdb414` | Location-is-the-primitive shipped whole; two new account pages have no nav entry |
| [2026-08-23](2026-08-23.html) | `e43b68e` → `407e552` | Legal and account-split shipped; the unsent email now has a competitor |
| 2026-08-11 | — | First audit. Not committed — it predates this directory and lives only as the Artifact it was published as |
