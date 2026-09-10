# Map reviews

Most recent cycle: [2026-09-10](2026-09-10.html) — parent ticket `SNOW-892`.

## Purpose

Longitudinal technical review of **one subsystem**: the map. `static/js/map*.js`
and the `*_core.js` modules it reads, `apps/public/api.py`'s map endpoints,
`home.html` and `_map_embed.html`, `static/css/map.css`.

It exists because the map is where the complexity is. It is the heaviest page
on the site, it holds 44,506 lines of first-party JavaScript across 75 files
loaded as classic scripts in a load-bearing order, and it is the one part of
the codebase where the tooling that guards everything else — `ruff`, `mypy`,
pre-commit — does not reach.

### How it differs from the two sibling series

Three review series, three questions, deliberately disjoint:

| Series | Asks |
|--------|------|
| `docs/code-reviews/` | Is the implementation sound, across the whole codebase? Drift, dead code, pattern consistency. |
| `docs/page-audits/` | Is each public page finished, reachable, and telling the truth? |
| `docs/map-reviews/` | Is the map subsystem well built — its architecture, failure modes, payload, and testability? |

The difference from `code-reviews` is depth against breadth. The 17-item
whole-codebase checklist cannot spend a cycle establishing that a 210-line
popup chain is unreachable, or measuring what the page actually ships. This
one reads one subsystem properly.

**Unlike `audit-pages`, this series creates Linear tickets.** A page audit
deliberately only reports, because a page audit that also edits pages stops
being a measurement. That reasoning does not carry here: these findings are
engineering work with owners and sizes, and a finding with no ticket is a
finding that gets rediscovered next cycle. Each cycle is a parent ticket with
one child per finding.

## Format

Each cycle is a **self-contained HTML file** (`YYYY-MM-DD.html`), matching
`docs/page-audits/`. No `<!doctype>`, `<html>`, `<head>` or `<body>` tags — it
is written to be published as an Artifact and still render opened from disk.
Fonts are linked from Google Fonts, never inlined as base64.

`bin/docs-lint` only scans `*.md`, and this directory is in its
`EXCLUDED_DIRS`, so neither the dated files nor this README carry frontmatter
or a routing-table entry — matching `code-reviews`, `page-audits`, `qa`,
`research` and `screenshots`.

## The document has two halves, and they age differently

Sections 01–07 **explain the subsystem** in plain English, for a technical
reader who does not write JavaScript. Sections 08–11 are **the review**.

That is not decoration. Most of what makes this subsystem hard is structural —
feature-state versus geometry, the shared global scope, one-writer-per-fact,
what a basemap swap destroys — and a finding list written for someone who
already holds all of that in their head is a finding list only its author can
act on.

The two halves are maintained differently:

- **The explainer is carried forward and corrected**, not rewritten. Each
  cycle checks its claims against the code and edits what has moved. The
  numbers in it are measurements and get re-measured; the mechanisms change
  rarely. Its delta is a diff, and a cycle where the explainer is unchanged is
  a normal cycle.
- **The review is rewritten every cycle**, as Closed / Unchanged / New. An
  audit that reads the same as last week's has failed.

## Cadence

Weekly, via the **"Weekly Snowdesk map review"** Routine, cron `0 6 * * 2`.
Tuesday keeps it clear of the Monday page audit and the Sunday-evening slot
that already holds the code review, the dependency updates and the competitor
scan. Routine crons are fixed UTC, so that is 07:00 under BST and 06:00 under
GMT — the same one-hour seasonal drift the page-audit Routine has.

- **Unattended:** the Routine calls `/audit-map routine`, which runs
  end-to-end with no approval gate — branch → re-verify → new findings →
  parent + child tickets → dated doc → PR.
- **On-demand:** run `/audit-map` interactively for a fresh read at any time.

## The rule that matters

**Verify every finding against the code, never against ticket state or
against a doc.** A ticket says what someone intended to ship. A doc says what
was true when it was written.

The first cycle is the cautionary tale. `docs/map-and-api.md` stated that
`api:region_summary` was "currently unused by the client". It is not —
`map_region_panel.js` fetches it, caches it per region and date, and
prefetches it. A reviewer trusting that line would have deleted a live
endpoint. The same cycle found the reverse case one file away: a popup
subsystem the same doc described as merely "untriggered" that is in fact
wholly unreachable, with nine live call sites that are permanent no-ops.

Corollary: when a check comes back clean, prove it twice. Greps miss
`{% include %}` chains, context built in views, and — in this subsystem
specifically — names reached through `window.snowdeskMapState` rather than as
bare identifiers.

## Findings are rated on the EAWS danger scale

The product's own visual language, reused for its own defects. It reads
instantly to anyone who works on this codebase, and it forces the distinction
that matters most: whether a user is affected.

| | Meaning |
|---|---|
| **1** | Cosmetic. Carrying cost only, no reader or user affected today. |
| **2** | Carrying cost. Slows or misleads the next person to read the code. |
| **3** | Structural. A missing defence, or a shape that makes defects likely. |
| **4** | User-visible. Someone gets a broken or degraded page. |
| **5** | Data or safety. Wrong avalanche information, or data loss. |

## Trivial vs spin-off rule

Inherited from `docs/code-reviews/README.md`, and applied the same way.

**Inline-fix** in the cycle's own PR anything that is single-file, no
behaviour change, no new tests required, and introduces no new abstraction.
The stale documentation line above is the canonical example: ticketing a
one-sentence correction and leaving it wrong for a sprint is worse than
fixing it in the pass that found it.

**Spin off** as a child ticket anything needing new tests, multi-file changes,
or a design decision.

**Watching** is the third disposition and is legitimate. A finding whose own
recommendation is "do this opportunistically" should not become a ticket that
sits open forever; record it, re-measure it next cycle, and raise a ticket if
it worsens.

## Layout of each cycle

- **Masthead** — cycle number, date, baseline commit → current, parent ticket
- **Figures strip** — the measurements, re-taken each cycle
- **01–07 Explainer** — the subsystem in plain English
- **08 What holds up** — the strengths, with evidence
- **09 Findings** — each rated, each with the check that established it
- **10 If only three things get done** — ranked, with reasons
- **11 What this cycle raised** — tickets, plus what was inline-fixed and what
  is being watched, each with the reason it was not ticketed

From cycle 02, section 09 splits into **Closed** (with the evidence that
proves it, and the ticket that did it), **Unchanged** (with a
re-verification note), and **New**.

## History

| Date | Baseline | Ticket | Headline |
|------|----------|--------|----------|
| [2026-09-10](2026-09-10.html) ([artifact](https://claude.ai/code/artifact/c6a9af11-14d4-43ae-accd-134e9541ab2d)) | — → `6824c52d` | `SNOW-892` | First cycle. Seven findings: a WebGL failure blanks the map silently and nothing reports it; 210 lines of unreachable popup code with nine live call sites into it; no type checking on 44k lines of JS. The payload turned out to be a non-finding — the deploy already minifies. |
