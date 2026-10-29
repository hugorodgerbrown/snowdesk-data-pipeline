---
name: scope
description: |
  Scope a Linear ticket: read the description, explore the codebase, produce a
  written scope, post it as a Linear comment (or update the scope the ticket
  already carries), move the ticket from Backlog to Todo and ask Codex for its
  one scope review with an @Codex review request (the `resolve-scope-review`
  skill takes it on from there). Parks a ticket that is redundant, out of date
  or low value in Deferred instead of scoping it. Use when the user asks to "scope SNOW-NN", "let's
  scope SNOW-NN", "work out SNOW-NN", "spec SNOW-NN", or any phrasing where
  they want a Linear ticket turned from a sentence into a proper scope. Do NOT
  use for: starting work on an already-scoped ticket, asking questions about a
  ticket without producing a scope, or any message that doesn't explicitly
  reference a SNOW-NN identifier.
allowed-tools: Agent, Bash, Read, Grep, Glob, EnterPlanMode, ExitPlanMode, mcp__Linear, mcp__claude_ai_Linear, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8
---

# Scope SNOW-$1

You are scoping Linear ticket SNOW-$1 in the Snowdesk codebase.

## Step 1 — Verify ticket state

Use the Linear MCP `get_issue` tool to fetch SNOW-$1.

**Hard precondition:** the ticket must be in `Backlog` state — that is
where every unscoped ticket lives. If it is in `Deferred`, tell the user it
has been parked and needs moving to `Backlog` first, and stop. If it is in
`Todo`, it is already scoped and waiting on Codex's review — point at
`resolve-scope-review` and stop. If it is in `Requires input`, `Ready for
dev`, `In Progress`, `In Review`, or `Done`, tell the user it has already
been scoped and stop. Do not proceed past this step on a wrong-state
ticket.

## Step 1b — Is it worth scoping at all?

Be rigorous before spending a scope on a ticket. Read it against the current
code, `docs/decisions/` and the rest of the backlog, and ask three questions:

- **Redundant?** The work is already done, has been superseded by another
  ticket or design, or duplicates an open ticket.
- **Out of date?** The ticket's premise no longer holds: the code it names has
  moved or gone, or the decision it rests on was reversed.
- **Low value?** What it buys is small for what it costs, or it serves a need
  nobody has yet (built for volume, scale or users that do not exist).

If any answer is yes, **propose parking it instead of scoping it**: state
`Deferred` (or `Duplicate` / `Done` where that is the true answer), plus a
one-line comment giving the reason, so a later reader can reopen it knowing
why it stopped. In interactive use the proposal goes through plan mode for
Hugo's approval, as a scope would. A ticket only partly out of date is still
scoped, trimmed to what remains, and the scope says what was dropped and why.

## Step 2 — Delegate scoping to the scoper agent

Invoke the `scoper` subagent via the Agent tool. Pass it:
- The Linear ticket ID (`SNOW-$1`)
- The Linear ticket git branch name as fetched
- The ticket title and description as fetched
- A clear instruction to produce a scope document and return it as the final
  message
- **Any scope the ticket already carries**, whether a scoping comment or an
  Approach / Touch list / Tests section in the description, as the starting
  point to verify and update rather than to replace

The scoper runs in an isolated context. It will explore the codebase to ground
the scope in what actually exists. Its working tokens stay in its own context —
you only see the final scope document.

## Step 3 — Present the scope via plan mode for approval

Once the scoper returns, present the scope through plan mode so it renders
in the sidebar UI with a proper approve / reject control — not as inline
conversational text the user has to reply to:

1. Call `EnterPlanMode` to enter plan mode. No research needed here — the
   scoper already did it. Edits and writes are blocked in plan mode, which
   keeps this step strictly read-only.
2. Immediately call `ExitPlanMode` with the **full scope document verbatim**
   as the `plan` argument. Do not summarise, reformat, or wrap it in extra
   commentary — the sidebar should show exactly what would be posted to
   Linear.

`ExitPlanMode` renders the scope in the sidebar with an approve / reject
control. The user reviews and acts via the UI.

If the user rejects with edits, revise and re-present via `ExitPlanMode`
again — each revision goes through the sidebar, not as inline text. Do not
invoke the scoper again unless the user explicitly asks for a fresh take —
refinements are usually small and you can handle them in the main thread.

**Hard gate:** do not post to Linear or transition the ticket until the
user has approved the scope via the sidebar.

## Step 4 — On approval, post and transition

When the sidebar approval comes through:

1. **One scope per ticket.** If the ticket has no scope yet, post it as a
   comment with `save_comment` (the comment's `issueId` is the internal `id`
   from `get_issue`, NOT the `SNOW-NN` identifier). If it already has a scope,
   **update that one and post no second**:
   - a scoping comment → `save_comment` with that comment's `id`;
   - a scope written into the description → rewrite those sections in place
     with `save_issue` (`patch`), leaving the rest of the description alone.
2. Check the ticket carries exactly one `Type` and one `Area` label. If either
   is missing or wrong now the scope has settled what the work actually is, fix
   it with `addLabels` / `removeLabels` in the same `save_issue` call. The
   tables in `.claude/skills/create-ticket/SKILL.md` decide them.
3. Move it to `Todo` in the same `save_issue` call. **Do not set a
   delegate.** Delegating an issue tells Codex to *implement* it, and in the
   test that framing overrode `AGENTS.md`.
4. Ask Codex for the review: post **the review request** from
   `docs/linear-workflow.md` → "The review request", verbatim, as a new
   top-level comment. The `@Codex` mention is what starts the Codex task.
   A mention-started run posts no comment when it starts: "Codex started"
   appears only in the ticket's activity log, which the API does not
   return, so do not wait for a start signal. The only evidence is Codex's
   `## Codex scope review` reply under the request, usually within a few
   minutes. If none has arrived by the next `resolve-scope-review` sweep,
   the review can be re-requested by posting the same comment again; the
   ticket stays in `Todo` either way.
5. Confirm to the user: "Scope posted, SNOW-$1 in Todo, review requested from
   Codex." The ticket goes no further in this skill — `Ready for dev` is set
   only by `resolve-scope-review`, after Codex has replied.

Open questions in the scope do not change this. They go to Codex with the
rest, and Codex may answer some from the code. `resolve-scope-review` sends
the rest to Hugo as `Requires input`.
