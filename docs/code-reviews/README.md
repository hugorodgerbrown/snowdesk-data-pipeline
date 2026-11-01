# Code review cycles

Most recent cycle: [2026-08-02](2026-08-02.md)

## Purpose

Longitudinal record of periodic code-review cycles. Each cycle is a Linear
ticket whose title follows the pattern:

    Code review pass — YYYY-MM-DD (drift, dead code, pattern consistency)

The deliverable for each cycle lands here as a dated markdown file
(`docs/code-reviews/YYYY-MM-DD.md`).

## Cadence

Weekly (automated Routine) or on-demand. One cycle per Linear ticket.

## Starting a new cycle

The cycle is automated by the **`audit-code` skill**
(`.claude/skills/audit-code/`), which drives the read-only
**`code-auditor` agent** (`.claude/agents/code-auditor.md`) through the
17-item checklist:

- **On-demand:** run `/audit-code` (interactive — proposes the doc,
  inline fixes, and tickets for approval before shipping). This is how
  every cycle to date has been run.
- **Unattended:** `/audit-code routine` runs end-to-end with no approval
  gate — branch → audit → dated doc → inline fixes → deduped child
  tickets → tox → PR. The skill supports this mode, but **no Routine is
  currently scheduled to call it**; wire one up with `/schedule` if you
  want the cycle to run itself.

The skill performs these steps each cycle:

1. Create this cycle's Linear ticket, cloned from the SNOW-269 template.
2. Create a branch: `chore/SNOW-NNN-code-review-YYYY-MM-DD`.
3. Run the 17-item audit checklist (via the `code-auditor` agent).
4. Create `docs/code-reviews/YYYY-MM-DD.md` (matching today's date) using
   the layout below.
5. Update the "Most recent cycle" pointer at the top of this file.

To run a cycle by hand without the skill, follow the same five steps.

## Trivial vs spin-off rule

**Inline-fix** anything that is:
- single-file,
- no behaviour change,
- no new tests required, and
- no new abstractions introduced.

**Spin off** as a child Linear ticket (with this cycle's ticket as parent)
for anything that requires new tests, multi-file changes, or design
decisions.

## The PR body names one ticket

A cycle's PR body carries counts, the tox baseline, a link to the dated doc
and the magic line `Closes SNOW-NNN`, and **no other SNOW id or Linear issue
link**. The GitHub–Linear integration closes a ticket linked from a merged PR
body, not only the one after `Closes`. That is how #331 (the 2026-07-12
cycle) closed SNOW-166 and SNOW-363–366 the moment it merged, with none of
their findings fixed. The child tickets are linked from the dated doc, which
the integration does not read. The `audit-code`, `audit-map` and
`audit-pages` skills all state the rule; do not put a child-ticket list back
in a PR body.

## Layout of each cycle's doc

Each `YYYY-MM-DD.md` should include the following sections in order:

- **Reviewer** — name of the reviewer
- **Branch** — branch name
- **Tox baseline** — pass/fail summary with coverage figure
- **Previous cycle** — link or "none (first cycle)"
- **Summary** — one paragraph on overall codebase health
- **Inline-fixed** — table of items fixed directly in this cycle
- **Spun off** — table of child tickets created
- **Watching** — items noted but deferred to future cycles
- **Checklist results** — one line per checklist item (17 items)
