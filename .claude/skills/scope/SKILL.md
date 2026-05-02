---
name: scope
description: |
  Scope a Linear ticket: read the description, explore the codebase, produce a
  written scope, post it as a Linear comment, and transition the ticket from
  Todo to Ready for dev. Use when the user asks to "scope SNOW-NN", "let's
  scope SNOW-NN", "work out SNOW-NN", "spec SNOW-NN", or any phrasing where
  they want a Linear ticket turned from a sentence into a proper scope. Do NOT
  use for: starting work on an already-scoped ticket, asking questions about a
  ticket without producing a scope, or any message that doesn't explicitly
  reference a SNOW-NN identifier.
allowed-tools: Task, Bash, Read, Grep, Glob, mcp__linear
---

# Scope SNOW-$1

You are scoping Linear ticket SNOW-$1 in the Snowdesk codebase.

## Step 1 — Verify ticket state

Use the Linear MCP `get_issue` tool to fetch SNOW-$1.

**Hard precondition:** the ticket must be in `Todo` state. If it is in
`Backlog`, tell the user it needs to be prioritised first and stop. If it
is in `Ready for dev`, `In Progress`, `In Review`, or `Done`, tell the user
it has already been scoped and stop. Do not proceed past this step on a
wrong-state ticket.

## Step 2 — Delegate scoping to the scoper agent

Invoke the `scoper` subagent via the Task tool. Pass it:
- The Linear ticket ID (`SNOW-$1`)
- The Linear ticket git branch name as fetched
- The ticket title and description as fetched
- A clear instruction to produce a scope document and return it as the final
  message

The scoper runs in an isolated context. It will explore the codebase to ground
the scope in what actually exists. Its working tokens stay in its own context —
you only see the final scope document.

## Step 3 — Show the scope to the user, ask for approval

Once the scoper returns, present the scope verbatim to the user. Then ask:

> "Approve this scope? Reply **go** to post it to Linear and move the ticket
> to Ready for dec,  **edit** to refine, or paste specific changes."

**Do not proceed without explicit approval.** "go", "yes", "ship it", "approve"
all count. Anything else means iterate.

## Step 4 — On approval, post and transition

When approved:

1. Post the scope as a comment on the Linear ticket using `save_comment` (the
   comment's `issueId` is the internal `id` from `get_issue`, NOT the `SNOW-NN`
   identifier).
2. Transition the ticket to `Ready for dev` using `save_issue` with the existing
   `id` and the new `stateId`.
3. Confirm to the user: "Scope posted, SNOW-$1 moved to Ready for dev."

If the user asked for edits, loop back to step 3 with the revised scope. Do not
invoke the scoper again unless the user explicitly asks for a fresh take —
refinements are usually small and you can handle them in the main thread.
