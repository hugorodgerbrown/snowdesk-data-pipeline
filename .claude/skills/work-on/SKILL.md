---
name: work-on
description: |
  Work a Linear ticket as far as it can go in this session: scope it and hand
  it to Codex (Backlog → Todo), or — once Codex has replied — resolve the
  review and implement it through to an open PR (Todo → Ready for dev → In
  Progress → In Review), with every approval gate intact. The ticket may be referenced as a bare number — every ticket in
  this project is SNOW-prefixed, so "work on 295" means SNOW-295. Use when
  the user says "work on SNOW-NN", "work on NN", "take NN through to
  review", "scope and implement NN", "do SNOW-NN end to end", or any
  phrasing that asks for both scoping and implementation of one ticket in
  one go. Do NOT use for: scoping only (`scope` skill), implementing an
  already-scoped ticket when the user only asks to implement (`implement`
  skill), resuming work on an existing branch, or any message without a
  ticket reference.
allowed-tools: Skill, mcp__Linear, mcp__claude_ai_Linear, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8
---

# Work on SNOW-$1

Run the `scope`, `resolve-scope-review` and `implement` skills in turn so
SNOW-$1 moves as far towards In Review as it can. Codex's scope review sits
between the first two and nothing in this session can wait for it, so a
fresh ticket takes two runs: the first scopes it and stops with Codex; the
second, once Codex has replied, resolves the review and implements. This
skill is a pure orchestrator: it
adds state dispatch and the handoff between phases, and nothing else. Each
phase's own rules — preconditions, approval gates, pause directives, stop
conditions — apply unchanged. Never duplicate or shortcut a sub-skill's
steps from here; invoke the skill and follow it.

## Step 0 — Parse the arguments

- **Ticket:** the canonical form is the bare number (`/work-on 295`), since
  every ticket in this project carries the `SNOW-` prefix. Accept
  `SNOW-295` too, but normalise to the bare number for passing to the
  sub-skills (they template it as `SNOW-$1`).
- **Pause directive:** any trailing instruction (e.g. *"work on SNOW-123
  but stop before pushing"*) belongs to the implement phase. Hold it and
  pass it through verbatim in step 3 — do not act on it during scoping.

## Step 1 — Dispatch on ticket state

Fetch the ticket with the Linear MCP `get_issue` tool and branch on state:

- **Backlog** → step 2 (scope), then stop: the ticket is with Codex.
- **Todo** with a `## Codex scope review` newer than the scope → step 2b
  (resolve the review), then step 3 if it reached `Ready for dev`.
- **Todo** with no review yet → stop: Codex has not replied. Say so; run
  `/work-on` again once it has.
- **Requires input** → stop: a question is waiting on Hugo. Once it is
  answered on the ticket, `/resolve-scope-review` takes it on.
- **Ready for dev** → already scoped and reviewed; go straight to step 3.
- **Deferred** → stop: the ticket is parked.
- **In Progress** → stop: work already exists. Suggest switching to the
  ticket's branch and using `/implement` in resume mode instead.
- **In Review / Done** → stop: nothing to do.

The sub-skills re-check state themselves — that redundancy is fine and
intentional. This dispatch exists so the user gets a clear answer up front
rather than a failure two steps in.

## Step 2 — Scope phase

Invoke the Skill tool with `skill: scope` and the ticket number as args,
and follow the scope skill in full — including presenting the scope via
plan mode and waiting for sidebar approval.

- If the user approves: the scope is posted to Linear and the ticket is in
  `Todo`, delegated to Codex. **Stop here** and say so: "Scope posted and
  with Codex. Run `/work-on NN` again once Codex has replied." Do not wait
  or poll for the review.
- If the user rejects and asks for revisions: stay in the scope phase
  until a revision is approved.
- If the user rejects and abandons (or the scope skill hits one of its
  hard stops): stop entirely. Do not implement a ticket whose scope was
  not approved.

## Step 2b — Resolve-review phase

Invoke the Skill tool with `skill: resolve-scope-review` and the ticket
number, and follow it in full, including its plan-mode approval of the
revised scope.

- Ticket ends in `Ready for dev` → continue to step 3 **without asking
  whether to continue** — proceeding is the whole point of this skill. A
  brief "Review resolved — moving on to implementation." is enough.
- Ticket ends in `Requires input` → stop and name the questions for Hugo.

## Step 3 — Implement phase

Invoke the Skill tool with `skill: implement` and args of the ticket
number plus any pause directive held from step 0. Follow the implement
skill in full: branch creation, ticket to In Progress, the plan-approval
hard gate, implementer/reviewer loop, query-count check, push, PR, ticket
to In Review.

The implement skill re-fetches the ticket and its comments, so it will
find the latest scope — the `## Scope revision` from step 2b if there is
one, otherwise the original scoping comment. If it cannot find a scoping
comment on a `Ready for dev` ticket, stop and surface that — something went wrong with the Linear
write; do not improvise a scope.

## Step 4 — Report

The implement skill's own final report stands. Add a one-line wrap-up for
the whole run, e.g.:

> "SNOW-NN: Codex's review resolved, PR opened at <url>, ticket now In
> Review."

or, on a first run:

> "SNOW-NN scoped and with Codex. Run `/work-on NN` again once it has
> replied."

## Failure handling

If any sub-skill stops at one of its hard stops (wrong state, missing
scoping comment, branch already exists, repeated review blocker), that
stop is final for this skill too. Report where in the chain it stopped and
why, so the user can resume with the narrower skill (`/scope`,
`/resolve-scope-review` or `/implement`) once the blocker is resolved. Never work around a
sub-skill's stop to keep the chain moving.
