---
name: codex-reviews-every-scope-once
description: Why every scope gets one Codex review before Ready for dev — AGENTS.md, delegate, resolve-scope-review, Requires input
status: current
last-reviewed: 2026-10-29
---

# Codex reviews every scope, once

## Decision

A scoped ticket goes to `Todo` and is delegated to Codex, never straight to
`Ready for dev`. Codex replies with one `## Codex scope review` comment, in a
fixed shape set by `AGENTS.md`. Claude then answers it with
`resolve-scope-review` and moves the ticket to `Ready for dev`, or to
`Requires input` when a question needs Hugo. There is one round.

## Why

- **A second model catches what the scoping model misses.** The scope is the
  handoff artefact: a wrong touch list or a test in the wrong layer costs a
  whole implementation cycle to find. Codex already reviews the PRs; reviewing
  the scope moves that check to where a fix costs one comment.
- **One round, so the loop ends.** Two agents that disagree can trade
  comments indefinitely. A fixed Claude → Codex → Claude turn always ends in
  a state Hugo can read off the board: `Ready for dev`, or `Requires input`
  with the question named. A `## Scope revision` newer than the review is the
  guard; nothing re-delegates.
- **Claude moves the status, Codex only writes.** Every status move lives in
  one place, the skills, which can be read and changed. Codex needs no write
  access to Linear workflow, and an unreadable reply is still handled: it is
  treated as `Changes requested`.
- **Delegation, not an `@Codex` mention.** The delegate field is Linear's
  mechanism for handing an issue to an agent. A mention posted through the
  API under Hugo's account may not trigger Codex.
- **A sweep, because Codex cannot hand back.** There is no first-party Claude
  agent in Linear for Codex to delegate to. A scheduled
  `resolve-scope-review routine` picks up reviewed tickets; the same command by
  hand covers the urgent ones. If a Claude agent for Linear appears, it could
  replace the sweep and nothing else would change.

## Consequences

- `Todo` now means "scoped, with Codex"; unscoped tickets live in `Backlog`.
  `scope` takes `Backlog`, not `Todo`.
- `work-on` cannot take a fresh ticket to a PR in one session: the first run
  stops with Codex, the second resolves the review and implements.
- `implement` builds from the latest `## Scope revision` when there is one.
- `AGENTS.md` carries the review brief, so the Codex that reviews PRs reads
  the same rules. It points at `CLAUDE.md` rather than copying it.
- The PR carries the agreed scope verbatim (`## Agreed scope`) and a
  `## Deviations from scope` list, so Codex's PR review checks the diff
  against the scope it approved. It is copied rather than linked because the
  PR review cannot be relied on to reach Linear, and a copy is the exact text
  approved, not whatever the ticket says by the time of review.
- Skipping the review takes an explicit yes from Hugo, recorded on the ticket.
