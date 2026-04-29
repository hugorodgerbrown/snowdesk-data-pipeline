---
name: linear-ticket-implementer
description: Use when picking up a scoped Linear ticket to implement it — i.e. the user says "implement SNOW-xxx" or equivalent. Covers the pickup sequence (fetch issue + comments, verify scoping comment exists, stop if missing), branch naming, the MCP move to In Progress (no push yet), PR title/body format including the `Closes SNOW-xxx` magic string, and when to stop and ask rather than push through. Do NOT use when creating or scoping a ticket — use linear-ticket-author for that.
---

# Linear ticket implementer

This skill governs how a scoped Linear ticket is **picked up, built, and
landed**. The full narrative lives in `docs/linear-workflow.md`; this skill
is the agent-facing rulebook.

Status transitions are split between the Linear MCP and the GitHub–Linear
integration:

- **Code (via Linear MCP)** moves the ticket from `Ready for dev` →
  `In Progress` at the **start** of implementation, immediately after
  creating the local branch. The branch is **not** pushed at this
  point — `In Progress` is a manual MCP move, not a side effect of a
  push.
- **GitHub integration** moves the ticket `In Progress` → `In Review`
  when the PR is opened, and `In Review` → `Done` when the PR is
  merged. Both triggers require `SNOW-xxx` in the branch name or PR
  body — get those references right and the post-implementation states
  stay in sync without manual nudging.

## When this skill applies

- The user says "implement SNOW-42" or equivalent.
- The user asks to continue work on a ticket that's already in progress.
- Any time the task is executing against an existing, scoped Linear ticket.

If the task is to *create* a ticket, *scope* a ticket, or *update* ticket
metadata, stop — that's the `linear-ticket-author` skill's job.

## Pickup sequence

Follow this order. Don't skip steps.

### 1. Fetch the issue and all comments

Use the Linear MCP server to fetch the issue *and* its comments. The
scoping comment is the handoff artefact — it contains the approach, touch
list, tests, and any open questions. You must read it before doing
anything else.

### 2. Verify the scoping comment exists and is clean

- **If the scoping comment is missing** → stop. Do not start work. Ask the
  user to scope the ticket in Chat first. If helpful, propose a scope
  based on the ticket description and ask the user to confirm or amend,
  but do not self-authorise a scope and proceed.
- **If the scoping comment has open questions** → stop. Ticket shouldn't
  have been promoted to `Ready for dev`. Surface the open questions and
  ask the user how to resolve them.
- **If the scoping comment is clean** → proceed.

### 3. Create the branch (locally — do not push)

Naming convention:

- Features: `feature/SNOW-xxx-short-kebab-description`
- Bug fixes: `fix/SNOW-xxx-short-kebab-description`
- Tooling/infra: `chore/SNOW-xxx-short-kebab-description`

Keep the slug under ~40 characters. It appears in the branch list and PR
title, so brevity matters.

Branch off the latest `main`. Don't branch off a stale local `main` —
pull first. **The branch stays local at this point**; do not `git push`.
The branch is pushed for the first time at PR-open (step 7).

### 4. Move the ticket to `In Progress` via the Linear MCP

Now that the local branch exists, move the ticket from `Ready for dev` →
`In Progress` using the Linear MCP `save_issue` tool (set
`state: "In Progress"`). This is the explicit handshake that work has
started — and the only mechanism that reflects it, since the branch is
deliberately not on GitHub yet.

Do **not** push the branch as a substitute for this step. The
GitHub–Linear integration in this workspace does not move tickets on
push — only on PR open — so an early push would not help and only adds
noise to the remote.

### 5. Implement

Follow the conventions in `CLAUDE.md`: render-model shape, management-command
design, i18n rules, test structure, etc. The scoping comment's touch list
and tests section are your guide.

### 6. Run the test suite

- `poetry run tox` — must pass cleanly before opening the PR.
- `npm run lh` — for any change touching a public page.

Fix every failure before opening the PR. Don't paper over flaky tests;
if a test is genuinely flaky, surface it and stop.

### 7. Push the branch and open the PR

This is the first push. Pushing the branch and opening the PR together
is what triggers the GitHub-driven `In Progress` → `In Review`
transition (the integration sees `SNOW-xxx` in the branch name and PR
body). See the PR format section below.

## Branch and commit conventions

- Branch name format above.
- **Commit subject prefix: `SNOW-xxx:`** — keeps the ticket reference in
  the git log even after a squash-merge rewrites the PR title. This
  matters for later archaeology.
- **One ticket per branch.** If implementation reveals work that needs its
  own ticket (newly discovered, not originally scoped), do not piggyback
  onto the current branch. Ask the user to spawn a follow-up ticket via
  the `linear-ticket-author` flow, and keep the current branch focused.

## PR format

### Title

`SNOW-42: short imperative summary`

Matches the branch minus the slug fluff. Example:
`SNOW-42: Add region search autocomplete`.

### Body

```markdown
Closes SNOW-42

## What
One-paragraph summary of the change.

## Why
Link back to the scoping comment on the Linear issue. One line on the
motivation if not obvious from the title.

## How
Bullet list of the notable implementation choices — anything a reviewer
would otherwise have to reverse-engineer from the diff.

## Testing
- What was added/changed in tests.
- Any manual verification done (URLs hit, management commands run).

## Screenshots / Lighthouse
For any change touching a public page: before/after screenshots and a
note on the latest `npm run lh` scores.
```

### The `Closes SNOW-xxx` line is mandatory

It's what closes the Linear ticket on merge. Omit it and the ticket
dangles in `In Review` forever. Do not omit it.

### The `In Review` transition

Triggered automatically by opening a PR whose branch name or body
references `SNOW-xxx`. No manual action needed.

## After merge

The Linear integration moves the ticket to `Done` when the PR merges into
`main`. No manual action required. Do not post a comment announcing
completion — the status transition is the announcement.

## When to stop and ask

These are the four situations where pushing through is worse than
stopping:

1. **Scoping comment missing** → ask the user to scope in Chat first.
   Optionally propose a scope for them to confirm or amend.
2. **Scoping comment has open questions** → the ticket shouldn't have been
   promoted. Surface the open questions and ask for resolution.
3. **Tests fail after implementation and the fix isn't obvious** → report
   the failure and stop. Do not paper over it, do not skip the test, do
   not mark it xfail without explicit user sign-off.
4. **Implementation reveals the scope was wrong** → post a comment on the
   Linear issue explaining what changed and why, then ask the user
   whether to proceed with the larger scope, re-scope, or split into a
   follow-up ticket. Don't silently expand the work.

## Anti-patterns

- **Don't move `In Review` or `Done` manually.** Those are GitHub-driven
  (PR open and PR merge respectively). Manual nudging causes drift.
  `In Progress` *is* a manual MCP move, made by Code at the start of
  implementation — that one is expected, not an anti-pattern.
- **Don't push the branch before opening the PR.** The push is what
  triggers `In Review`; pushing earlier just creates a "stale Draft"
  flicker on the remote and doesn't help status. `In Progress` is set
  by the MCP move in step 4, not by the push.
- **Don't omit `Closes SNOW-xxx` from the PR body.** The ticket won't
  close on merge.
- **Don't squash unrelated work onto one branch.** One ticket, one branch,
  one PR.
- **Don't skip the pre-PR test run.** `poetry run tox` must pass locally
  before the PR opens — CI failing on the PR is wasted round trips.
