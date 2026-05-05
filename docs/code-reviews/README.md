# Code review cycles

Most recent cycle: [2026-05-05](2026-05-05.md)

## Purpose

Longitudinal record of periodic code-review cycles. Each cycle is a Linear
ticket whose title follows the pattern:

    Code review pass — YYYY-MM-DD (drift, dead code, pattern consistency)

The deliverable for each cycle lands here as a dated markdown file
(`docs/code-reviews/YYYY-MM-DD.md`).

## Cadence

Monthly or on-demand. One cycle per Linear ticket.

## Starting a new cycle

1. Clone the previous cycle's Linear ticket description (or the SNOW-112
   template stored in Linear) and create a new ticket.
2. Create a branch: `chore/SNOW-NNN-code-review-YYYY-MM-DD`.
3. Run the 17-item audit checklist from the ticket description.
4. Create `docs/code-reviews/YYYY-MM-DD.md` (matching today's date) using
   the layout below.
5. Update the "Most recent cycle" pointer at the top of this file.

## Trivial vs spin-off rule

**Inline-fix** anything that is:
- single-file,
- no behaviour change,
- no new tests required, and
- no new abstractions introduced.

**Spin off** as a child Linear ticket (with this cycle's ticket as parent)
for anything that requires new tests, multi-file changes, or design
decisions.

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
