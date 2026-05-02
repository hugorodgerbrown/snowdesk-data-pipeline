---
name: start
description: |
  Start work on a scoped Linear ticket: create a feature branch, push it
  upstream, transition the ticket to In Progress, and enter plan mode to
  produce an implementation plan. Use when the user says "start SNOW-NN",
  "begin SNOW-NN", "let's start work on SNOW-NN", "kick off SNOW-NN", "pick up
  SNOW-NN", or any clear intent to begin coding a Ready-for-dev ticket. Do NOT
  use for: scoping a ticket (use scope skill), implementing already-planned
  work (use implement skill), or any message without an explicit SNOW-NN
  reference.
allowed-tools: Bash, Read, mcp__linear
---

# Start work on SNOW-$1

## Step 1 — Verify ticket is Ready

Use Linear MCP `get_issue` to fetch SNOW-$1.

**Hard precondition:** state must be `Ready for dev`. If it is `Todo` or
`Backlog`, tell the user the ticket needs scoping first and stop. If it is `In
Progress`, ask whether they want to resume existing work (in which case they
should switch branches manually, not run this skill). If `In Review` or `Done`,
stop.

Read the ticket description AND comments — the scope lives in a comment posted
by the scope skill. Read the codebase and confirm that the existing scope is
correct and can be implemented. Store the ticket git branch name (returned from
the `get_issue` call).

## Step 2 — Create local branch

Run, in a single bash call:

```bash
git checkout main && \
git pull --ff-only && \
git checkout -b {{git_branch_name}}
```

If branch creation fails because the branch already exists , stop and ask the
user how to proceed. Do not silently switch to an existing branch.

## Step 3 — Transition ticket to In Progress

Use Linear MCP `save_issue` with the ticket's internal `id` and the
`In Progress` state. Confirm the transition succeeded.

## Step 4 — Enter plan mode and produce a plan

Now produce an implementation plan. Do NOT write code yet. The plan should
cover:

- **Files to touch** — concrete paths in the Snowdesk repo
- **Order of changes** — what gets built first, second, third
- **Test strategy** — what tests to write or update, what existing tests cover
  this surface
- **Risks / open questions** — anything that might bite during implementation

Read the relevant parts of the codebase to ground the plan. Reference the scope
(from the Linear comment) for acceptance criteria.

## Step 5 — Wait for plan approval

Present the plan and ask:

> "Approve the plan? Reply **go** to start implementation, or push back on
> anything you want changed."

**Hard gate:** do not write any code, do not invoke the implementer, do not exit
plan mode without explicit approval. If the user pushes back, revise the plan
and ask again.

When approved, tell the user: "Plan approved. Run `implement` (or just say
'implement') when ready."

Do not auto-chain into implementation — give the user a beat to context-switch
if they want to.
