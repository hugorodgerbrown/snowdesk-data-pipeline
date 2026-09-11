---
name: create-ticket
description: >
  Create a new Linear ticket, or update an existing ticket's description,
  labels, priority, or estimate. Use when a conversation has produced work
  worth tracking — "make a ticket for that", "log this as a bug", "turn this
  discussion into tickets" — and whenever creating a follow-up ticket mid-task.
  Covers the two creation modes (one ticket vs many from one discussion), the
  decomposition rule (one ticket per independently-shippable unit), the
  four-section scoping comment contract, the rule that only a clean scoping
  comment promotes a ticket to `Ready for dev`, and the Linear MCP parameter
  traps (priority/estimate enums, exact state names, how to set blocks /
  blockedBy / relatedTo / parentId). Do NOT use to scope a ticket that already
  exists (`scope`) or to implement one that is already scoped (`implement`).
allowed-tools: Read, mcp__Linear, mcp__claude_ai_Linear, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8
---

# Create a Linear ticket

This skill governs how tickets are **created and updated** in the Snowdesk
Linear workspace (team prefix `SNOW-`). The full narrative lives in
`docs/linear-workflow.md`; this skill is the agent-facing rulebook.

It is the front of the lifecycle: this skill makes the ticket, `scope` takes
an existing ticket from `Todo` to `Ready for dev`, and `implement` builds it.

Linear is the source of truth. Nothing of substance lives only in a chat
window — if it matters, it goes on the ticket.

## When this skill applies

- Creating a new ticket (from a planning conversation, a bug just noticed,
  a discussion that decomposed into work).
- Posting a scoping comment on an existing ticket to move it to
  `Ready for dev`.
- Updating a ticket's description, labels, priority, or estimate.

Posting a **project** status update is a different job — that's the
`post-project-update` skill, which gathers the shipped work and posts it.

If the user is asking to **implement** a ticket that's already scoped,
stop — that's the `implement` skill's job.

## The two creation modes

### Mode 1 — Single ticket, scoped in one pass

Conversation has produced a single, clear piece of work. Create the issue
*and* post the scoping comment *and* move to `Ready for dev` in one
sequence. No intermediate `Todo` stop.

### Mode 2 — Multiple tickets from one discussion

Conversation decomposed into several independent pieces of work. For each
piece:

- Create the issue with title, label, priority, and a one-paragraph
  description.
- If the approach is settled in the discussion → post the scoping comment
  and move to `Ready for dev`.
- If the approach is still fuzzy → leave at `Todo` and add a note naming
  the open question.

After creation, report back a summary like:

> Created SNOW-102 (Ready for dev), SNOW-103 (Ready for dev),
> SNOW-104 (Todo — needs decision on cache strategy).

## Decomposition rules

- **One ticket per independently-shippable unit.** "Add feature X + refactor
  the surrounding module" is two tickets, not one.
- **Don't promote underspecified work.** If the approach for a ticket isn't
  settled, it stays at `Todo` with a note on what's missing — even if the
  adjacent tickets are ready. Underspecified tickets leaking into
  implementation is the failure mode this rule prevents.
- **Title, Type + Area labels, priority, one-paragraph description are
  mandatory** at creation time. The scoping comment is in addition to the description,
  not instead of it.

## Labelling

Every ticket carries **exactly one `Type` and exactly one `Area`**, plus
zero or more `Non-functional` flags. Both mandatory labels are set at
creation time — a ticket with no Area is invisible to every backlog filter
that matters.

### Type — what kind of change

| Label         | Test                                                                        |
|---------------|-----------------------------------------------------------------------------|
| `Bug`         | Behaviour is wrong today.                                                   |
| `Feature`     | A capability that does not exist yet.                                       |
| `Improvement` | Product code gets better; no new capability. Refactors, cleanups.           |
| `Chore`       | No product-code behaviour change: deps, tooling, CI, docs, test coverage.   |

The `Improvement`/`Chore` line is behaviour: if a user could in principle
notice the result, it is `Improvement`.

### Area — which part of the product

| Label       | Covers                                                                       |
|-------------|------------------------------------------------------------------------------|
| `Bulletin`  | The bulletin document and page: render model, day character, CAAML fidelity, glossary, reading guide, calendar, compressed views. |
| `Map`       | The map page and what is drawn on it: layers, basemaps, scrubber, slope angle, location beacon, map-anchored panels. |
| `Offline`   | The PWA shell and what makes it work without a network: service worker, cache strategy, downloaded areas, mutation queue, IndexedDB, sync state. |
| `Weather`   | The Open-Meteo domain: the Location-anchored `Weather` model, its fetch and backfill, and the surfaces rendering it. Not CAAML provider weather prose, which is `Bulletin`. |
| `Ingest`    | Getting provider data in and keeping it right: the SLF / ALBINA / Météo-France fetchers and translators, pipeline commands, and the regions and resorts reference data they key against. |
| `Planning`  | What a user saves and shares: favourites, routes, trips, field observations. |
| `Account`   | Sign-in, passkeys, the settings page, the account profile, session and identity. |
| `Alerts`    | Anything sent out unprompted: web push, digest and notification email, and the rules deciding when they fire. |
| `Site`      | The shell every page shares and the pages that are none of the above: nav, /help/, page metadata and share cards, error pages, design system, component library. |
| `Platform`  | Everything that is not the product: CI, tox, linters, dependencies, deploys, migrations, test infrastructure and coverage, developer tooling, analytics and telemetry plumbing. |

Pick where the **work happens**, not every area it touches. A test-seeding
ticket for the weather surfaces is `Platform`, not `Weather`; a download
ticket for slope tiles is `Offline`, not `Map`.

### Non-functional — optional cross-cutting flags

`Security`, `Performance`, `Design` (needs visual design work, not just
implementation), `Accessibility`. Zero or more, never instead of an Area.

`Performance` is a workspace-level label shared with the Ski Parrainage
team, so it sits outside the `Non-functional` group in Linear's picker
while meaning the same thing.

## Scoping comment contract

This is the handoff artefact. The implementer reads it on pickup and
inherits full context. Every scoping comment has these four sections:

- **Approach** — 2–4 sentences on the chosen solution.
- **Touch list** — files/modules expected to change.
- **Tests** — what will be covered.
- **Open questions** — anything still undecided.

**Promotion rule:** if `Open questions` is non-empty, the ticket stays at
`Todo`. Only a clean scoping comment (no open questions) moves a ticket to
`Ready for dev`.

## Status transitions this skill owns

This skill writes to Linear up to `Ready for dev`. Post-commit states
(`In Progress`, `In Review`, `Done`) are handled by the GitHub–Linear
integration and are not this skill's concern.

| Status          | When this skill sets it                                     |
|-----------------|-------------------------------------------------------------|
| `Backlog`       | Ticket created but not yet triaged                          |
| `Todo`          | Ready to be picked up, approach not yet settled             |
| `Ready for dev` | Clean scoping comment posted (no open questions)            |

## Linear MCP tool gotchas

These are the traps. Get them wrong and the call silently does the wrong
thing or fails opaquely.

### `save_issue` — priority is an integer enum

| Value | Meaning |
|-------|---------|
| 0     | None    |
| 1     | Urgent  |
| 2     | High    |
| 3     | Medium  |
| 4     | Low     |

Pass an integer, not a string. `"High"` is not valid; `2` is.

### `save_issue` — estimate is Fibonacci

| Value | T-shirt |
|-------|---------|
| 1     | XS      |
| 2     | S       |
| 3     | M       |
| 5     | L       |

No `4`, no `8`. Stick to the scale.

### Issue states must match exact team-configured names

`save_issue` takes state by name. The name must match exactly — capitalisation,
spacing, punctuation. If unsure, call `list_issue_statuses` for the team
first rather than guessing. Guessing wastes a round trip when the call fails.

### Relationships: set them, don't only describe them

`save_issue` takes `blocks`, `blockedBy`, `relatedTo` and `parentId`, each as
an array of identifiers (`["SNOW-693", "SNOW-839"]`) — `parentId` as a single
one. All four verified 2026-09-11 across SNOW-908/909/910/911 — `relatedTo`
and `blockedBy` on create, `blocks` and `parentId` on update — each confirmed
by reading the issue back with `get_issue` + `includeRelations: true`.

An earlier revision of this guide said `blocks` "does not reliably accept any
known format" and prescribed prose in the description instead. That is out of
date, and prose is not a substitute: a mention gets you an undirected
`relatedTo` (see below), never the direction. "A blocks B" and "B blocks A"
are the same sentence to Linear unless you set the relation. Set it **and**
say it in the description — the relation drives the UI, the sentence carries
the reason:

> Blocked by SNOW-95 — the API contract lands there first.

Four things worth knowing:

- **A bare `SNOW-NNN` in a description creates a `relatedTo` relation.** The
  server rewrites the identifier into an issue element and links the pair —
  no relation parameter involved. Deleting the mention afterwards does **not**
  unlink them; only `removeRelatedTo` does, and it clears both edges. Probed
  2026-09-11 on SNOW-908/692 and reverted. Two consequences: a ticket that
  discusses its neighbours in prose acquires relations to all of them, and a
  relation you did not set is not evidence of a bug.
- **`blocks` and `relatedTo` are one slot per pair.** Setting `blocks` on a
  pair already linked as `relatedTo` converts it; the pair does not end up
  carrying both.
- **Relationship params are append-only.** Existing links are never removed by
  a later call. Use `removeBlocks` / `removeBlockedBy` / `removeRelatedTo` to
  take one off.
- **`parentId` does not confer the parent's project.** A child keeps whatever
  project it had — including none, which puts it outside every project view
  its siblings appear in. Set `project` explicitly when parenting.

Always read the issue back with `includeRelations: true` after setting one.

### Project updates are not a comment, not a document

When asked to "post a project update" for a Linear project, post it as a
Linear **project update** — not a comment on a project, not a document.
Downgrading to a comment puts it in the wrong place and breaks the project
update feed.

Use **`save_status_update`** with `type: "project"`. (An older version of
this guide said `create_project_update` was unavailable and told you to hand
the text back for manual pasting — that is out of date; `save_status_update`
is exposed and is what `post-project-update` calls. Pass the prior update's
`id` to edit rather than duplicate.)

## House style

- **Decisions before copy.** If a decision is still being made in the chat,
  don't write ticket copy yet. Fold the decision into the description once
  settled, then write.
- **Descriptions are paragraphs, not bullet soup.** A one-paragraph
  description that reads like a human wrote it beats a bullet list of
  fragments.
- **Link ruthlessly.** Reference related tickets by `SNOW-xxx`; Linear
  auto-links them.
- **No chat-window residue.** Don't paste "per our conversation" or
  "as we just discussed" into ticket copy. Write as if the ticket is the
  first time the reader is seeing the problem.

## Verification step

After creating multiple tickets via Mode 2, **remind the user to verify in
Linear** before handing any of them to the implementer. The chat window is
not the source of truth; Linear is, and mistakes are cheapest to catch
before the implementer picks up a malformed ticket.

## When to stop and ask

- The user asks for a ticket but the approach in the discussion is genuinely
  unclear → propose a `Todo` ticket with the open question called out,
  rather than guessing the approach.
- The user asks to promote a ticket to `Ready for dev` but there are open
  questions in the scoping comment → refuse the promotion and name the
  open questions.
- Priority is ambiguous from context → ask once, don't guess. Labels are
  not ambiguous: the Type and Area tables above decide them.
