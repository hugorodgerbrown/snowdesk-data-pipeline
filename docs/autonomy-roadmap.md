---
name: autonomy-roadmap
description: Target state for Claude and Codex working without Hugo except on design decisions; human gates in scope, implement, merge-prs, release
status: draft
last-reviewed: 2026-11-07
---

# Autonomy roadmap

**This is a target, not a current rule.** The rules in force are in
[`AGENTS.md`](../AGENTS.md) and [`CLAUDE.md`](../CLAUDE.md). Gates come off one
at a time, each in its own change, and a gate moves to "Removed" below when it
does.

## The objective

Claude and Codex together act autonomously. Hugo's input is needed when a decision
is made, specifically on design and UX approval, or where he has asked to be
involved.

## Decisions so far

Hugo's answers of 3 November 2026 on the end state:

- **A design decision** is new or changed behaviour: new surfaces, changed flows,
  layout or interaction changes. Fixes that restore intended behaviour, and
  changes built from existing partials and tokens, need no approval.
- **Design is approved before building.** The ticket goes to `Requires input` with
  a mock or description, and nothing is built until Hugo approves.
- **Other questions the code cannot answer** go to Hugo through `Requires input`.
- **Tickets start autonomously**, through a routine that picks up `Ready for dev`.
  Merging to staging needs no approval.
- **Production releases stay Hugo's decision**, after Claude's staging check.

## Gates still in place

| Gate | Where | End state |
|---|---|---|
| User-scope approval in the sidebar (what changes, not how) | `scope` Step 3; `resolve-scope-review` Steps 4 and 6; `work-on` Steps 2 and 2b | Removed, unless the ticket holds a design decision |
| Batch confirmation; only Hugo can start it | `merge-prs` | Unsettled |
| Nothing picks up `Ready for dev` | Missing routine | A routine that takes one ticket per run |

`release` keeps its gates: only Hugo starts it, and it asks before opening the PR.

## Removed

- **Plan approval in the sidebar**, 5 November 2026. Hugo reviews the scope, not the
  implementation plan. Once the scope is signed off and Codex has reviewed it,
  `implement` writes the plan and builds with no stop. If Codex's review changes
  the scope, Claude decides whether to refer back to Hugo.
- **Commit message approval**, 3 November 2026. A rule that depends on which skill
  is running is one Hugo cannot see, so commits no longer wait in any session.
  Merging follows the same rule, and Claude says one line when it starts a skill.
- **The merge prompt**, 3 November 2026. Hugo took `gh pr merge*` off the `ask` list
  in `.claude/settings.json`.
