---
name: raise-pr
description: |
  Raise a pull request for the current feature branch and transition the Linear
  ticket to In Review. Use when the user says "raise the PR", "open a PR",
  "raise PR", "ship it", "create the PR", or any clear intent to open a pull
  request for a completed feat/snow-NN branch. Do NOT use for: pushing
  intermediate commits, draft PRs (unless explicitly requested), or any branch
  not matching feat/snow-NN.
allowed-tools: Bash, Read, mcp__linear
---

# Raise PR

## Step 1 — Verify branch state

```bash
git branch --show-current
git status --short
git log origin/main..HEAD --oneline
```

Extract the ticket number (`SNOW-NN`).

**Hard preconditions:**
- Current branch includes ticket number (`SNOW-NN`)`. If not, stop.
- Working tree clean. If dirty, ask the user whether to commit, stash, or abort.
- At least one commit ahead of `origin/main`. If zero commits, stop — there's
  nothing to PR.

## Step 2 — Push latest commits

```bash
git push
```

## Step 3 — Generate PR title and body

Read:
- The Linear ticket scope (from `get_issue` + comments)
- The commit log on this branch

Produce:
- **Title:** `SNOW-NN: <short summary, sentence case, no trailing period>`
- **Body:** structured as:
  - **What** — one-paragraph summary of the change
  - **Why** — link to the Linear ticket, brief context
  - **How** — bullet list of the main changes
  - **Testing** — what tests cover this, anything to verify manually
  - `Closes SNOW-NN` (Linear's GitHub integration auto-transitions on merge)

Show the user the title and body before opening. Ask:

> "Open PR with this title and body? Reply **go**, or paste edits."

## Step 4 — Open the PR

On approval, use `gh pr create` with the title and body. Capture the PR URL from
the output.

## Step 5 — Transition ticket to In Review

Use Linear MCP `save_issue` with the ticket's internal `id` and `In Review`
state. Post a comment on the ticket with the PR URL via `save_comment`.

## Step 6 — Report

> "PR opened: <url>. SNOW-NN is now In Review."

The user takes it from here — review, merge. Linear's GitHub integration will
auto-transition to Done on merge if the `Closes SNOW-NN` line is in the PR body.
