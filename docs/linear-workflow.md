# Linear workflow

Linear (team prefix `SNOW-`) is the issue source of truth. The Linear MCP
server is connected in both Claude Chat and Claude Code — the same
workspace, just different surfaces. **Nothing of substance lives only in
a chat window.** If it matters, it lives in the Linear issue.

## Status lifecycle

Every ticket ends up passing through these states. A ticket may enter
the lifecycle at `Backlog`, `Todo`, or `Ready for dev` depending on
which entry point produced it (see **Entry points** below). Each
transition is tied to a concrete event so status reflects reality
without manual nudging:

| Status         | Transition trigger                                        | Who moves it |
|----------------|-----------------------------------------------------------|--------------|
| `Backlog`      | Issue created, not yet triaged                            | Human / Chat (MCP) |
| `Todo`         | Ready to be picked up, not yet scoped                     | Human / Chat (MCP) |
| `Ready for dev`| Scoping comment posted; approach settled                  | Chat (MCP)   |
| `In Progress`  | Feature branch pushed to GitHub                           | GitHub integration |
| `In Review`    | PR opened against `main`                                  | GitHub integration |
| `Done`         | PR merged                                                 | GitHub integration |

The GitHub–Linear integration handles `In Progress`, `In Review`, and
`Done` automatically when the branch name or PR body references
`SNOW-xxx`. Chat writes to Linear via MCP when creating tickets, posting
scoping comments, and moving status up to `Ready for dev`; it does not
touch the post-commit states.

## Entry points

There are two ways a ticket reaches `Ready for dev`. Both end with a
scoped Linear issue — which is what Code consumes. Code doesn't care
which path produced it.

### Path A — Ticket first, then scope

The ticket already exists in Linear (you created it from the UI, or it
came out of a planning session, or a previous Chat spawned it). Open a
Chat session and pull it:

> "Pull SNOW-42 and let's scope it."

Discuss the approach — data model changes, edge cases, the Django view
vs HTMX partial split, service boundaries, test shape. When the
approach is settled:

> "Post the scope as a comment on SNOW-42 and move it to Ready for dev."

Chat appends the scoping comment via MCP (see **Scoping comment
contract** below) and moves status.

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
   > autocomplete'. Scope it directly into Ready for dev using what
   > we've just discussed."

   Chat creates the issue *and* posts the scoping comment in one go,
   then moves it to `Ready for dev`. No intermediate `Todo` stop.

2. **Multiple tickets from one discussion.** When the conversation
   decomposes into several pieces of work:

   > "Emit Linear tickets for the work we just scoped. One per
   > independently-shippable unit. Scope each one and set status:
   > anything with a settled approach goes to Ready for dev, anything
   > still fuzzy goes to Todo with a note on what's missing."

   Chat creates each issue via MCP, assigns labels and priority,
   writes the scoping comment on the ones with settled approach, and
   reports back a summary:

   > Created SNOW-102 (Ready for dev), SNOW-103 (Ready for dev),
   > SNOW-104 (Todo — needs decision on cache strategy).

**Rules for chat-spawned tickets:**

- One ticket per independently-shippable unit of work. Don't bundle
  "add feature + refactor the surrounding module" into one ticket —
  that's two.
- Title, label, priority, and a one-paragraph description are
  mandatory at creation time. The scoping comment goes on top, not
  instead.
- If the approach for a given ticket isn't settled in the discussion,
  create it at `Todo` with a note naming the open question. Don't
  promote it to `Ready for dev` just because the adjacent tickets are
  ready — that's how underspecified work leaks into implementation.
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
- **Open questions** — anything still undecided. If non-empty, the
  ticket stays at `Todo`; only a clean scoping comment (no open
  questions) moves the ticket to `Ready for dev`.

## Implement in Claude Code

In the Snowdesk repo, open Claude Code and say:

> "Implement SNOW-42."

Code's expected sequence:

1. **Fetch** the issue and all comments via the Linear MCP server —
   including the scoping comment. Do not start work without reading
   the scoping comment; if it's missing, stop and ask the user to
   scope in Chat first.
2. **Create a branch** named `feature/SNOW-42-short-kebab-description`
   off the latest `main`. Keep the slug under ~40 chars; it appears in
   the branch list and PR title.
3. **Push the branch** to GitHub immediately (empty or with a first
   commit). Pushing a branch whose name contains `SNOW-42` is what
   triggers the Linear integration to move the ticket to `In Progress`
   — do this early so status reflects "work has started" accurately.
4. **Implement** the work on that branch, following the conventions in
   CLAUDE.md (render-model shape, management-command design, i18n
   rules, test structure, etc.).
5. **Run `poetry run tox`** and fix every failure before opening the PR.
   Run `npm run lh` for any change touching a public page.
6. **Open a PR** (see next section).

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
```

The `Closes SNOW-42` magic comment in the PR body is what closes the
Linear ticket on merge — do not omit it. The `In Review` transition is
triggered by opening the PR (Linear watches for `SNOW-xxx` in the
branch name or PR body).

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
