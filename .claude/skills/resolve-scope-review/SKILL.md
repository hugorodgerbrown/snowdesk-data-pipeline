---
name: resolve-scope-review
description: |
  Claude's second and last round on a Linear ticket's scope: read Codex's
  scope review, answer every suggestion and question, post the revised scope,
  and move the ticket from Todo to Ready for dev — or to Requires input when a
  question needs Hugo. Also takes a Requires input ticket the rest of the way
  once Hugo has answered on it. Use when the user says "resolve the review on
  SNOW-NN", "Codex has replied on NN", "pick up Codex's comments", or
  "/resolve-scope-review NN". Also used by a scheduled Routine — when invoked
  with `routine` (or `sweep` / `--no-approval`) and no ticket, it sweeps every
  Todo ticket Codex has reviewed, with no approval gate. Do NOT use for:
  scoping a ticket for the first time (`scope`), implementing one (`implement`),
  or asking Codex for a second review — there is one round.
user-invocable: true
# Three renderings of the one Linear connector — the tool prefix differs per
# client surface (CLI, cloud, Desktop); list all three. See .claude/README.md.
allowed-tools: Agent, Bash, Read, Grep, Glob, EnterPlanMode, ExitPlanMode, mcp__Linear, mcp__claude_ai_Linear, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8
---

# Resolve the scope review on SNOW-$1

The scope loop is **Claude → Codex → Claude, once** (see
`docs/linear-workflow.md` → "Scope review"). `scope` was the first Claude
turn and left the ticket in `Todo`, delegated to Codex. Codex replied with a
`## Codex scope review` comment. This skill is the last turn: it ends with the
ticket in `Ready for dev` or `Requires input`, never back with Codex.

## Modes

- **Interactive** (default) — a ticket number was given. The revised scope is
  shown through plan mode for approval before anything is posted.
- **Routine** — args contain `routine`, `sweep` or `--no-approval`. With no
  ticket number, sweep (Step 0). No plan mode, no approval gate: post, move,
  and report. A routine never touches a `Requires input` ticket — those wait
  for Hugo.

## Step 0 — Sweep (routine mode with no ticket only)

`list_issues` for team `SNOW` in state `Todo`. For each one, run Steps 1–5
and keep going on a failure — one bad ticket does not stop the sweep. Skip,
without writing anything, every ticket that has no Codex review newer than
its latest scope (Step 1): Codex has not replied yet.

Finish with a one-line report per ticket processed, e.g.:

> SNOW-1301 → Ready for dev (Codex approved).
> SNOW-1302 → Requires input (1 question for Hugo: cache lifetime).
> SNOW-1303 → skipped (no Codex review yet).

## Step 1 — Read the ticket

`get_issue` for SNOW-$1, then `list_comments`.

**State:**

- `Todo` → continue.
- `Requires input` → interactive only. Hugo's answers are the comments Hugo
  wrote after the latest `## Scope revision`. If there are none, say the
  ticket is still waiting on Hugo and stop. Otherwise go to Step 3 with those
  answers in place of Codex's review — there is no Codex step here.
- `Backlog` → not scoped yet; point at `scope` and stop.
- Anything later → nothing to do; stop.

**Find the two comments that matter:**

- **The scope** — the latest comment carrying the four scoping sections
  (Approach, Touch list, Tests, Open questions) that is **not** a
  `## Scope revision`: the scope Codex was asked to review.
- **The review** — the latest comment headed `## Codex scope review` that is
  newer than the scope.

No review newer than the scope → Codex has not replied. Say so and stop (in
a sweep, skip). Do not re-delegate; the delegation is already standing.

A Codex reply that is not in the agreed shape (no `**Verdict:**` line) is
still a review: read it as `Changes requested` and treat each point it makes
as a suggestion.

A `## Scope revision` already newer than the review → this ticket has had its
round. Never write a second revision; that guard is what keeps the loop to one
round. If the ticket is still in `Todo`, an earlier run posted the revision
and then failed to move it: finish Step 5.2 from that revision's Open
questions and stop.

## Step 2 — Approved

`**Verdict:** Approved` with no Suggestions and no Questions:

1. `save_issue` — state `Ready for dev`, `delegate: null`.
2. Do not post a comment. The review is on the ticket already, and its Notes
   are there for `implement` to read.
3. Report: "SNOW-$1 → Ready for dev (Codex approved)."

## Step 3 — Changes requested: answer every point

Ground each answer in the code. Delegate to the `scoper` agent when the
review asks for something that needs real exploration; otherwise read the
files directly.

- **Each suggestion** is **accepted** (the scope changes) or **declined**
  (with the reason, citing the code or a rule). Never ignore one.
- **Each question** is **answered** from the code, the ticket or
  `docs/decisions/`, or it is **for Hugo**: a product decision, a priority
  call, anything the code cannot settle. Do not guess a product decision to
  avoid `Requires input`.
- Carry forward the scope's own Open questions the same way.

Then write the **revised scope**: the full four-section scoping comment with
the accepted changes folded in, so `implement` reads one complete scope, not
a diff. Its Open questions holds only the questions for Hugo.

The comment body:

```markdown
## Scope revision

This supersedes the scope above, after Codex's review.

**Approach** — …
**Touch list** — …
**Tests** — …
**Open questions** — None. | 1. <question for Hugo>

### Response to Codex's review
1. Suggestion 1 — accepted: <what changed>.
2. Suggestion 2 — declined: <why, citing code or rule>.
3. Question 1 — answered: <answer>.
4. Question 2 — for Hugo (see Open questions).
```

## Step 4 — Approve (interactive only)

`EnterPlanMode`, then at once `ExitPlanMode` with the comment body verbatim
as the plan. Do not post until it is approved. On rejection with edits,
revise and re-present; do not consult Codex again.

## Step 5 — Post and move

1. `save_comment` on the ticket with the body.
2. `save_issue`, in one call:
   - Open questions empty → state `Ready for dev`, `delegate: null`.
   - Open questions non-empty → state `Requires input`, `delegate: null`,
     `assignee: "hugo@rodger-brown.com"`, so it lands in Hugo's queue.
3. Report: "SNOW-$1 → Ready for dev" or "SNOW-$1 → Requires input (n
   questions for Hugo: …)".

A `Requires input` ticket resolved by Hugo's answers (Step 1) ends here too:
post a `## Scope revision` that folds in those answers, and move it to `Ready for
dev` — or leave it in `Requires input` and say which question is still open.

## Stops

- `Requires input` does not exist on the team (`list_issue_statuses`) →
  stop and say Hugo needs to create it in Linear (Settings → Teams → SNOW →
  Workflow, in the Unstarted group). Never substitute another state.
- `save_issue` fails after the revision is posted → report it. The ticket
  stays in `Todo`, and the next run finishes the move from the posted
  revision (Step 1) rather than writing another.
