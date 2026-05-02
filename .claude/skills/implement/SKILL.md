---
name: implement
description: |
  Implement an approved plan: invoke the implementer agent to write code, then
  the reviewer agent to check it, looping until the reviewer is satisfied. Use
  when the user says "implement", "go", or "run the implementation" AND the
  current branch is not main and has an approved plan in context. Do NOT use
  for: writing code without a plan, ad-hoc edits to existing code, or any work
  not tied to a Snowdesk feature(/chore/bug) branch.
allowed-tools: Task, Bash, Read, Edit, Write, Grep, Glob
---

# Implement

## Step 1 — Verify branch state

Run:

```bash
git branch --show-current
git status --short
```

**Hard preconditions:**

- Current branch name must include SNOW-NN . If on `main` or anywhere else,
  stop and tell the user to run `start` first.
- Working tree should be clean (no uncommitted changes from a previous session).
  If dirty, ask the user how to proceed.

Extract the ticket number from the branch name for downstream use.

## Step 2 — Invoke the implementer agent

Use the Task tool to invoke the `implementer` subagent. Pass it:
- The Linear ticket number & branch name
- A reminder to consult the Linear ticket scope (in comments) and the approved
  plan from this session's context
- An instruction to commit incrementally with conventional commit messages, run
  tests as it goes, and report back when the plan is fully implemented

The implementer runs in its own context. Its exploration and intermediate work
do not pollute the main thread.

## Step 3 — Invoke the reviewer agent

Once the implementer reports done, invoke the `reviewer` subagent in a fresh
forked context. Pass it:
- The ticket number
- The branch name
- An instruction to check: tests pass, linter clean, scope acceptance criteria
  met, no obvious mistakes or omissions

The reviewer returns one of: **clean**, **blockers** (with specifics), or
**suggestions** (non-blocking).

## Step 4 — Loop if needed

- If reviewer returns **blockers**: pass them back to the implementer with a
  request to address them. Re-invoke the reviewer when done. Loop until clean or
  until the same blocker appears twice (in which case stop and surface to the
  user — something is structurally wrong).
- If reviewer returns **clean** or **suggestions only**: stop the loop.

## Step 5 — Report back

Tell the user:

> "Implementation complete. Reviewer says: [clean / suggestions]. Run `raise-pr`
> (or just say 'raise the PR') when ready."

If the reviewer left non-blocking suggestions, list them so the user can decide
whether to address them before the PR or punt to a follow-up.

Do not auto-chain into raising the PR — that's the user's call.
