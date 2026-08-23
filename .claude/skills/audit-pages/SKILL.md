---
name: audit-pages
description: >-
  Audit the product state of every public Snowdesk page — what each page says,
  what it does, what it promises that the code doesn't deliver, and how the
  pages link to one another. Produces a dated self-contained HTML artefact at
  `docs/page-audits/YYYY-MM-DD.html`, publishes it as an Artifact, and opens a
  PR. Use when the user says "audit the pages", "page audit", "what state are
  the pages in", or references the page-audit series. Supports unattended
  Routine use — when invoked with `routine` (or `weekly` / `--no-approval`) in
  the args, runs end-to-end with no approval gate. Do NOT use for the
  whole-codebase drift audit (that's `audit-code`) or a per-diff review (that's
  `code-review`).
user-invocable: true
# Both Linear server names: the local MCP config and the claude.ai connector
# (UUID), which is the only one a remote Routine session sees. See .claude/README.md.
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Artifact, mcp__linear-server, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8
---

# Page audit

A recurring audit of the **product** state of the public site. Not a code
review: `audit-code` asks whether the implementation is sound, this asks
whether each page is finished, reachable, and honest.

Full conventions, format rules and section layout:
[`docs/page-audits/README.md`](../../../docs/page-audits/README.md).

## The one rule that matters

**Verify every finding against the code, never against ticket state.** A
ticket marked Done says what somebody intended to ship. Only the code says
what shipped. The 2026-08-23 audit found two tickets closed whose findings
were genuinely fixed — and would have wrongly reported a third as unfixed had
it trusted a single grep instead of following the include chain into the
partial that actually rendered it.

Corollary: when a check comes back clean, prove it twice before writing it
down. Greps miss partials, `{% include %}` chains, and context built in views.

## Routine mode

Invoked from the weekly Routine. **Trigger phrases** — any of:

- The invocation args contain `routine`, `weekly`, or `--no-approval`.
- The first user message looks like a scheduled-task header.

In routine mode: skip the approval gate and run end-to-end. No human is
watching, so the discipline matters more, not less — never overstate a
closure, never report a finding fixed without the evidence in hand, and if a
check is ambiguous say so rather than resolving it optimistically.

## Step 1 — Establish the baseline

```bash
date -u +%F                                  # audit date, names the doc + branch
ls docs/page-audits/*.html | sort | tail -1  # previous audit
```

Read the previous audit in full. Its **Unchanged** table and **Recommendation**
section are this audit's checklist. Note its baseline commit — the masthead
records `<previous baseline> → <current HEAD>`.

Bring the working tree to current `main` before reading any code:

```bash
git fetch origin main
git checkout -B chore/page-audit-$(date -u +%F) origin/main
git rev-list --count <previous-baseline>..HEAD
git diff --name-only <previous-baseline>..HEAD | wc -l
```

## Step 2 — Re-verify every open finding

Work the previous audit's findings one at a time. For each, run the check that
originally established it and record the current answer. Common shapes:

| Finding kind | How to re-verify |
|---|---|
| A page promises something unbuilt | grep the copy; confirm the command/task/schedule entry still absent |
| A field is captured but never shown | grep the field name across **all** template dirs, then follow includes |
| A route is orphaned | count inbound links from `templates/`, `apps/*/templates/`, `static/js/` |
| A doc has drifted | check its `last-reviewed` and the specific stale claim |
| Content is missing a provider/case | count mentions per provider in the template |

Then pull ticket state from Linear for context — **after** the code checks,
never instead of them. A finding that is fixed in code but whose ticket is
open is a ticket-hygiene note; a finding whose ticket is Done but is unfixed in
code is a real problem and belongs in the audit.

## Step 3 — Find what's new

The previous audit cannot have anticipated the last week. Look for:

- New apps, routes, models, or templates (`git diff --stat`, `ls apps/`)
- New decision docs (`docs/decisions/`) — especially ones that supersede
  another, which usually means an older ticket is now obsolete
- New public surfaces that arrived without a page, an index, or a nav entry —
  the failure mode this series exists to catch
- Changes to what the product claims about itself (`/terms/`, `/help/`,
  the reading guide, the copy on any CTA)

## Step 4 — Draw the consequences

The section that makes this worth reading. Look for connections neither the
tickets nor the docs can see from inside themselves:

- A decision this week that **answers** an open question blocking an old ticket
- A decision that **obsoletes** a ticket before it is built
- A public claim that has shipped **ahead of** the engineering backing it
- A gap that has changed character — e.g. a missing feature that has acquired
  a competitor

## Step 5 — Write the doc

Write `docs/page-audits/YYYY-MM-DD.html`, following the format and section
layout in [`docs/page-audits/README.md`](../../../docs/page-audits/README.md).

Copy the design system from the previous audit verbatim — the token block, the
type scale, the pill and finding-stripe classes. It is theme-aware in all three
states (bare `:root`, `prefers-color-scheme`, and `[data-theme]`); do not
re-derive it, and do not redefine a colour inside a media or `[data-theme]`
block. Fonts are a Google Fonts `<link>`, never inlined base64.

Then update the "Most recent audit" pointer and the History table in
`docs/page-audits/README.md`.

## Step 6 — Publish and ship

1. `uv run tox -e docs-lint` — the only env this touches. Run it, don't assume.
2. Publish the doc as an Artifact (new file path each week → new URL).
3. Commit with `--author="Claude <noreply@anthropic.com>"`, subject
   `docs: page audit YYYY-MM-DD`.
4. Push and open a **draft** PR. The body should carry the short-version
   paragraph and the Artifact URL, so the PR is readable without opening the
   file.

## Do not

- **Create or edit Linear tickets.** This audit reports; it does not act.
  Findings that warrant tickets are raised with the user, or in routine mode
  listed in the PR body under "worth ticketing" for a human to decide.
- **Fix anything you find.** A page audit that also edits pages stops being a
  measurement.
- **Restate the site from scratch.** After the first audit, the delta is the
  product. An audit that reads the same as last week's has failed, even if
  every word in it is true.
