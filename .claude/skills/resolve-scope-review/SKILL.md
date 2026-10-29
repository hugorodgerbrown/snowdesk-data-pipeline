---
name: resolve-scope-review
description: |
  Claude's second and last round on a Linear ticket's scope: read Codex's
  scope review, answer every suggestion and question, revise the scoping
  comment in place, reply under Codex's review, and move the ticket from Todo
  to Ready for dev — or to Requires input when a question needs Hugo. Also
  takes a Requires input ticket the rest of the way once Hugo has answered on
  it. Use when the user says "resolve the review on SNOW-NN", "Codex has
  replied on NN", "pick up Codex's comments", "I've answered NN", or
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
turn: it posted the scoping comment, asked Codex for a review with the
`@Codex` review request, and left the ticket in `Todo`. Codex replied under
that request with a `## Codex scope review` comment. This skill is the last
turn: it ends with the ticket in `Ready for dev` or `Requires input`, never
back with Codex.

**There is one scope per ticket.** This skill never posts a second scoping
comment: it edits the original in place, so the ticket always has exactly one
scope and `implement` always reads that one.

## Modes

- **Interactive** (default) — a ticket number was given. The revised scope is
  shown through plan mode for approval before anything is written.
- **Routine** — args contain `routine`, `sweep` or `--no-approval`. With no
  ticket number, sweep (Step 0). No plan mode, no approval gate: write, move,
  and report. A routine never touches a `Requires input` ticket — those wait
  for Hugo.

## The comments this skill reads

Claude posts to Linear through Hugo's account, so a comment's author cannot
tell Claude from Hugo. Claude's comments are told apart by their opening
marker instead:

| Comment | Author | Opens with |
|---|---|---|
| **The scope** | Hugo's account | the four sections: Approach, Touch list, Tests, Open questions. Occasionally they are written into the description instead of a comment |
| **The review request** | Hugo's account | `@Codex This is a **scope review only` |
| **The review** | **Codex** | `## Codex scope review` |
| **Claude's response** | Hugo's account, a reply in the review's thread | `### Response to Codex's review` |
| **Hugo's answers** | Hugo's account | anything without one of the markers above or below |
| **Claude's answers note** | Hugo's account, a reply under Hugo's answer | `### Answers folded in` |

The review is the latest `## Codex scope review` comment **authored by
Codex**. Any other Codex comment is ignored, in particular the summary of a
run that implemented the ticket by mistake, which is not a review.

## Step 0 — Sweep (routine mode with no ticket only)

`list_issues` for team `SNOW` in state `Todo`. Since the scope workflow, Todo
holds only tickets that are waiting on Codex or have just been reviewed, so
the list is short. If it is not, narrow it with `updatedAt: "-P2D"`: a Codex
reply updates the ticket, and a run that has been missing for days is caught
by the next one by hand. For each ticket, read
its comments and skip it without writing anything if no review authored by
Codex is there yet. Run Steps 1–5 on the rest. Keep going on a failure: one
bad ticket does not stop the sweep.

Finish with a one-line report per ticket processed, e.g.:

> SNOW-1301 → Ready for dev (Codex approved).
> SNOW-1302 → Requires input (1 question for Hugo: cache lifetime).

## Step 1 — Read the ticket

`get_issue` for SNOW-$1, then `list_comments`, and find the comments in the
table above.

**State:**

- `Todo` → continue below.
- `Requires input` → interactive only; go to **Step 6**.
- `Backlog` → not scoped yet; point at `scope` and stop.
- Anything later → nothing to do; stop.

**In `Todo`:**

- No review authored by Codex → Codex has not replied. Say so and stop (in a
  sweep, skip). Do not post a second review request.
- **Claude's response already exists in the review's thread** → this ticket
  has had its round. Never respond twice: that guard is what keeps the loop
  to one round. The ticket still being in `Todo` means an earlier run
  responded and then failed to move it, so finish Step 5.2 from the scope's
  current Open questions and stop.
- A review with no `**Verdict:**` line is still a review: read it as
  `Changes requested`, and treat each point it makes as a suggestion.

## Step 2 — Approved

`**Verdict:** Approved` with no Suggestions and no Questions:

1. `save_issue`: state `Ready for dev`.
2. Write no comment. The scope stands as it is, and the review's Notes are on
   the ticket for `implement` to read.
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

Then write two things:

1. **The revised scope.** This is the full scoping comment with the accepted
   changes folded in, so `implement` reads one complete scope rather than a
   diff. It opens with the line
   `_Revised after Codex's review, <YYYY-MM-DD>._`, and its Open questions
   holds only the questions for Hugo (`None.` if there are none).
2. **The response.** This is a reply in the review's thread:

   ```markdown
   ### Response to Codex's review
   1. Suggestion 1 — accepted: <what changed in the scope>.
   2. Suggestion 2 — declined: <why, citing code or rule>.
   3. Question 1 — answered: <answer>.
   4. Question 2 — for Hugo (now in the scope's Open questions).
   ```

## Step 4 — Approve (interactive only)

`EnterPlanMode`, then at once `ExitPlanMode` with the revised scope and the
response verbatim. Do not write anything until they are approved. If they
are rejected with edits, revise and re-present them. Do not consult Codex
again.

## Step 5 — Write and move

1. Edit the scope in place: `save_comment` with **the scoping comment's
   `id`** and the revised body. If the scope lives in the description, use
   `save_issue` with a `patch` over those sections instead. Then post the response with `save_comment`,
   using `parentId` = the review's `id`. Write the scope first: the response
   is the guard, so it must not exist before the scope it describes.
2. `save_issue`, in one call:
   - Open questions empty → state `Ready for dev`.
   - Open questions non-empty → state `Requires input`, with
     `assignee: "hugo@rodger-brown.com"` so it lands in Hugo's queue.
3. Report: "SNOW-$1 → Ready for dev" or "SNOW-$1 → Requires input (n
   questions for Hugo: …)".

## Step 6 — Requires input: fold in Hugo's answers

Hugo's answers are his comments, either replies or top-level comments, that
are newer than Claude's response and carry no marker from the table.

- **Recovery first.** Claude's answers note may already be newer than every
  answer, and the scope's Open questions may be `None.`. That means an earlier
  run folded the answers in and then failed to move the ticket. Move it to
  `Ready for dev` and stop.
- No answers → say the ticket is still waiting on Hugo, name the open
  questions, and stop.
- Otherwise apply each answer to the scope, as Step 3 does for a review
  point. An answer can change the scope's shape, for example by bringing work
  in or ruling it out, so re-check the touch list and tests against the code
  rather than only deleting the question. Then:
  1. Show the revised scope through plan mode for approval, as in Step 4.
  2. Edit the scoping comment in place. Its first line becomes
     `_Revised after Codex's review and Hugo's answers, <YYYY-MM-DD>._`
  3. Reply under Hugo's answer with
     `### Answers folded in`, plus one line per answer naming what it changed.
  4. Open questions now empty → `Ready for dev`. Still not empty → leave it in
     `Requires input` and name the questions still open.

Codex is not asked again at any point in this step.

## Stops

- `Requires input` does not exist on the team (`list_issue_statuses`) →
  stop and say Hugo needs to create it in Linear (Settings → Teams → SNOW →
  Workflow, in the Unstarted group). Never substitute another state.
- `save_issue` fails after the response is posted → report it. The ticket
  stays where it was, and the next run finishes the move (Step 1's guard, or
  Step 6's recovery) rather than writing anything again.
