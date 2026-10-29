---
name: linear-workflow
description: Linear ticket lifecycle — states incl. Requires input, scoping comment, Codex scope review, branch naming, Closes SNOW-xxx
status: current
last-reviewed: 2026-10-29
---

# Linear workflow

Linear (team prefix `SNOW-`) is the issue source of truth. The Linear MCP
server is connected in both Claude Chat and Claude Code — the same
workspace, just different surfaces. **Nothing of substance lives only in
a chat window.** If it matters, it lives in the Linear issue.

## Status lifecycle

Every ticket ends up passing through these states. A ticket enters the
lifecycle at `Backlog` (unscoped) or `Todo` (scoped in the same pass
that created it — see **Entry points** below). Nothing enters at
`Ready for dev`: every scope goes through Codex's review first (see
**Scope review** below). Each transition is tied to a concrete event so
status reflects reality without manual nudging:

| Status          | Transition trigger                                            | Who moves it |
|-----------------|---------------------------------------------------------------|--------------|
| `Backlog`       | Issue created, not yet scoped                                 | Human / Claude (MCP) |
| `Todo`          | Scoping comment posted; Codex's review requested               | Claude — `scope` / `create-ticket` |
| `Requires input`| Codex's review left a question only Hugo can answer           | Claude — `resolve-scope-review` |
| `Ready for dev` | Codex approved, or Claude answered every point in its review  | Claude — `resolve-scope-review` |
| `In Progress`   | Local branch created at start of implementation               | Code (MCP)   |
| `In Review`     | PR opened against `main`                                      | GitHub integration |
| `Done`          | PR merged to `main`                                           | GitHub integration |

`Deferred` is the parking state for work that is not going to be done
soon; nothing moves a ticket out of it but Hugo.

`Done` means the PR merged to `main` — the work is complete and on
**staging**. It does **not** mean the ticket has shipped to production:
production deploys only when `release` is fast-forwarded to `main`. Which
tickets reached production is recorded in the **GitHub
Release** created on each production deploy (CalVer tag, notes generated
from the `SNOW-xx:` PR titles). See [`deployment.md`](deployment.md).

Status moves up to and including `In Progress` happen via the Linear
MCP — Claude creates and scopes tickets into `Todo`, Codex reviews them,
Claude moves them to `Ready for dev` (or `Requires input`), then Code
moves the ticket to `In Progress` immediately after creating the
local branch (no push required, and no push happens at that point).
The GitHub–Linear integration handles only `In Review` (when the PR
opens) and `Done` (when the PR merges); both of those triggers require
`SNOW-xxx` in the branch name or PR body.

## Entry points

There are two ways a ticket reaches `Todo`, scoped and with Codex. Both
then go through the same **Scope review** to `Ready for dev`, which is
what Code consumes. Code doesn't care which path produced it.

### Path A — Ticket first, then scope

The ticket already exists in Linear (you created it from the UI, or it
came out of a planning session, or a previous Chat spawned it). Open a
Chat session and pull it:

> "Pull SNOW-42 and let's scope it."

Discuss the approach — data model changes, edge cases, the Django view
vs HTMX partial split, service boundaries, test shape. When the
approach is settled:

> "Post the scope as a comment on SNOW-42."

Claude appends the scoping comment via MCP (see **Scoping comment
contract** below), moves the ticket to `Todo` and posts the review
request for Codex. The `scope` skill does this. If the ticket already
carries a scope, `scope` updates that one instead of posting a second,
and if the ticket is redundant, out of date or low value it proposes
parking it in `Deferred` instead of scoping it.

### Path B — Chat first, then tickets

A conversation exposes work that should become one or more tickets.
This is common: you're discussing a page redesign, a refactor, or a
bug you just noticed, and the discussion naturally decomposes into
distinct pieces of work. In this mode Chat creates the tickets *in
Linear* — not in a bulleted list in the chat window — so they enter
the same system with the same shape as every other ticket.

Two sub-patterns:

1. **Single ticket from a single discussion.** When scope is already
   clear from the conversation:

   > "Create a Linear ticket for this — title: 'Add region search
   > autocomplete'. Scope it using what we've just discussed."

   Claude creates the issue *and* posts the scoping comment in one go,
   then moves it to `Todo` and posts the review request. No `Backlog` stop.

2. **Multiple tickets from one discussion.** When the conversation
   decomposes into several pieces of work:

   > "Emit Linear tickets for the work we just scoped. One per
   > independently-shippable unit. Scope each one and set status:
   > anything with a settled approach goes to Codex, anything still
   > fuzzy stays in Backlog with a note on what's missing."

   Claude creates each issue via MCP, assigns labels and priority,
   writes the scoping comment on the ones with settled approach, hands
   those to Codex, and reports back a summary:

   > Created SNOW-102 (Todo, with Codex), SNOW-103 (Todo, with Codex),
   > SNOW-104 (Backlog — needs decision on cache strategy).

**Rules for chat-spawned tickets:**

- One ticket per independently-shippable unit of work. Don't bundle
  "add feature + refactor the surrounding module" into one ticket —
  that's two.
- Title, label, priority, and a one-paragraph description are
  mandatory at creation time. The scoping comment goes on top, not
  instead.
- If the approach for a given ticket isn't settled in the discussion,
  create it at `Backlog` with a note naming the open question. Don't
  scope it just because the adjacent tickets are ready — that's how
  underspecified work leaks into implementation.
- After Chat reports the list of created tickets, **verify in Linear**
  that they look right before handing any of them to Code. The chat
  window is not the source of truth; Linear is.

## Scoping comment contract

Whichever entry point produced the ticket, the scoping comment Chat
writes to Linear has the same shape. This is the handoff artefact —
Code reads it on pickup and inherits full context without re-scoping.

- **Approach** — 2–4 sentences on the chosen solution.
- **Touch list** — files/modules expected to change.
- **Tests** — what will be covered.
- **Open questions** — anything still undecided. The ticket goes to
  Codex either way, and Codex may answer some from the code. Only a
  scope with no open questions left after the review reaches
  `Ready for dev`; anything still open sends it to `Requires input`.

## Scope review

Every scope gets one review from Codex before implementation: **Claude →
Codex → Claude, once.** At the end the ticket is in `Ready for dev` or
`Requires input` — never back with Codex for a second round.

```
 Backlog ──scope──▶ Todo + "@Codex This is a scope review only…"
                      │
                      ▼
            ## Codex scope review   (AGENTS.md → "Scope review")
                      │
       ┌──────────────┴───────────────┐
   Approved                     Changes requested
       │                              │
       │                  resolve-scope-review: answer every point,
       │                  revise the scoping comment IN PLACE,
       │                  reply "### Response to Codex's review"
       │                              │
       │               ┌──────────────┴──────────────┐
       │        nothing left open          a question for Hugo
       ▼               ▼                             ▼
          Ready for dev                       Requires input
                                      (assigned to Hugo; answer on the
                                       ticket, then /resolve-scope-review)
```

1. **Claude scopes.** `scope` (or `create-ticket`) posts the four-section
   comment, moves the ticket to `Todo`, and posts **the review request**
   below as a new comment. The `@Codex` mention starts a Codex task, and
   the comment is that task's prompt.
2. **Codex reviews.** Codex runs a cloud task on the repo, reads
   `AGENTS.md`, and replies under the request with one `## Codex scope
   review` comment: `**Verdict:** Approved` or `Changes requested`, then
   Suggestions, Questions and non-blocking Notes. It writes no code and
   moves nothing.
3. **Claude resolves.** `resolve-scope-review` reads the review.
   - On Approved, it moves the ticket to `Ready for dev`.
   - On Changes requested, it accepts or declines each suggestion with a
     reason, and answers each question from the code or marks it for Hugo.
     It then **edits the scoping comment in place**, so the ticket keeps
     exactly one scope and `implement` builds from it. It replies under the
     review with `### Response to Codex's review`, one line per point.
   - If no questions are left, the ticket goes to `Ready for dev`.
     Otherwise it goes to `Requires input`, assigned to Hugo.
4. **Hugo answers** (`Requires input` only) in a comment on the ticket,
   then runs `/resolve-scope-review NN`. That folds the answers into the
   same scoping comment, replies `### Answers folded in`, and moves the
   ticket to `Ready for dev`. Codex is not asked again.

**There is one scope per ticket.** Nothing posts a second scoping
comment. Every revision, whether after Codex's review or after Hugo's
answers, edits the original in place and says so in its first line.

### The review request

`scope`, `create-ticket` and `audit-map` post this verbatim, as a new
top-level comment, once the scope is on the ticket:

```markdown
@Codex This is a **scope review only — do not implement this ticket.** Do not edit any file, commit, create a branch or open a pull request.

Follow the "Scope review (Linear)" section of `AGENTS.md` in the repo: review this ticket's scope (the scoping comment, or the Approach / Touch list / Tests / Open questions sections of the description) against the code, then reply with a single comment in exactly the shape that section gives, starting with the heading `## Codex scope review` and a `**Verdict:**` line. Leave the product questions you cannot answer from the code as Questions — do not decide them. Your reply is the whole deliverable.
```

**Never delegate the issue to Codex instead.** Linear's delegation hands
Codex the ticket's title and description as a task to *do*. In the
2026-10-29 test that framing beat `AGENTS.md`: Codex implemented the
ticket and settled a product question on its own. The comment above
worked first time on both tickets.

**What starts step 3.** Codex cannot hand a ticket back, because there
is no first-party Claude agent in Linear. So two things pick up a
reviewed ticket:
- a scheduled Claude task that runs `resolve-scope-review routine`. It
  acts only on `Todo` tickets Codex has reviewed, and
  never touches a `Requires input` ticket;
- the same command, run by hand, for any ticket wanted sooner.

**What keeps it to one round.** Once Claude's `### Response to Codex's
review` reply exists under the review, the round is done.
`resolve-scope-review` never responds twice, and nothing asks Codex
again. All of this is read from the thread of the **latest** review
request. A ticket that is parked and scoped again gets a fresh request,
and its old review and response are ignored, so they cannot pass for the
new scope's round.

**Setup this depends on** (one-off, in Linear and Codex):
- Codex for Linear installed in the workspace.
- A `Requires input` state on the SNOW team, in the Unstarted group.
- **The Codex Cloud (Legacy) environment for this repo**, which is the one
  Linear's integration uses. GitHub PR review uses the other environment
  system. Configure the legacy one like this:
  - an **empty setup script**. `bin/build.sh` is Render's deploy build:
    it migrates, fetches GeoIP data with the MaxMind key and syncs flags,
    and must never run in a review container. The stale `./build.sh`
    script failed both of the first test runs;
  - **no secrets**, and no environment variables are needed. A review
    reads code and runs nothing
    ([why no provider keys](decisions/cloud-sessions-hold-no-provider-credentials.md));
  - the review itself needs no internet access from the container, since
    it only reads the checked-out code. It has only been tested with
    access on. If you turn it off and a review then completes without
    posting a reply, turn it back on, restricted to `linear.app`.

Why it is built this way:
[`decisions/codex-reviews-every-scope-once.md`](decisions/codex-reviews-every-scope-once.md).

## Implement in Claude Code

In the Snowdesk repo, open Claude Code and say:

> "Implement SNOW-42."

Code's expected sequence:

1. **Fetch** the issue and all comments via the Linear MCP server —
   including the scoping comment (revised in place after Codex's review,
   when it asked for changes). Do not start work without reading
   the scope; if it's missing, stop and ask the user to scope first.
2. **Create a branch** named `feature/SNOW-42-short-kebab-description`
   off the latest `main`. Keep the slug under ~40 chars; it appears in
   the branch list and PR title. The branch stays local at this point —
   do not push it.
3. **Move the ticket to `In Progress`** via the Linear MCP. This is the
   handshake that signals "work has started"; the branch deliberately
   has not been pushed yet, so the status move has to happen in MCP.
4. **Implement** the work on that branch, following the conventions in
   CLAUDE.md (render-model shape, management-command design, i18n
   rules, test structure, etc.).
5. **Run `uv run tox`** and fix every failure before opening the PR.
   Run `npm run lh` for any change touching a public page.
6. **Push the branch and open a PR** — this is the first push, and is
   what triggers the GitHub-driven `In Review` transition (see next
   section).

## Open the PR

PR title format: `SNOW-42: short imperative summary` (matches the
branch, minus the slug fluff — e.g. `SNOW-42: Add region search
autocomplete`).

PR body must include:

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

## Agreed scope
The scoping comment as it stands, verbatim — Approach, Touch list,
Tests, Open questions. A link to the Linear comment, and the Notes from Codex's scope
review.

## Deviations from scope
Every way the diff departs from the agreed scope, and why. `None.` if
there are none.
```

The **Agreed scope** section is what Codex's PR review checks the diff
against (`AGENTS.md` → "Review guidelines"). Copying it in rather than
linking it means the review does not depend on Codex reaching Linear, and
the text it checks against is exactly the text it approved. A departure
from the scope that is not listed under **Deviations** is a finding.

The `Closes SNOW-42` magic comment in the PR body is what closes the
Linear ticket on merge — do not omit it. The `In Review` transition is
triggered by opening the PR — that is also the point at which the
branch is first pushed to GitHub, so the integration sees the
`SNOW-xxx` reference in the branch name and PR body together and moves
the ticket from `In Progress` → `In Review` automatically.

## After merge

The Linear integration moves the ticket to `Done` when the PR merges
into `main`. No manual action required.

## Branch and commit conventions

- Branch name: `feature/SNOW-xxx-short-description` for features,
  `fix/SNOW-xxx-short-description` for bug fixes,
  `chore/SNOW-xxx-short-description` for tooling/infra.
- Commit subject prefix: `SNOW-xxx:` — keeps the ticket reference in
  the git log even after squash-merge rewrites the PR title.
- One ticket per branch. If implementation reveals work that needs
  its own ticket (newly discovered, not originally scoped), create
  it via Path B from Chat — don't piggyback onto the current branch.

## When Code should stop and ask

- Scoping comment missing on the ticket → ask the user to scope in Chat
  first. Propose the scope if possible, and ask the user to confirm or
  amend.
- Tests fail after implementation and the fix isn't obvious → report
  the failure and stop, don't paper over it.
- The implementation reveals the scope was wrong → post a comment on
  the Linear issue explaining what changed and why, then ask the user
  whether to proceed, re-scope, or split into a follow-up ticket.
