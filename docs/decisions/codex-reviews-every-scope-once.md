---
name: codex-reviews-every-scope-once
description: Why every scope gets one Codex review before Ready for dev — AGENTS.md, @Codex review request, resolve-scope-review, Requires input
status: current
last-reviewed: 2026-10-29
---

# Codex reviews every scope, once

## Decision

A scoped ticket goes to `Todo`, never straight to `Ready for dev`, and Claude
asks Codex for a review in an `@Codex` comment. Codex replies with one
`## Codex scope review` comment, in a fixed shape set by `AGENTS.md`. Claude
then answers it with `resolve-scope-review`, revising the one scoping comment
in place, and moves the ticket to `Ready for dev`, or to `Requires input`
when a question needs Hugo. There is one round.

## Why

- **A second model catches what the scoping model misses.** The scope is the
  handoff artefact: a wrong touch list or a test in the wrong layer costs a
  whole implementation cycle to find. Codex already reviews the PRs; reviewing
  the scope moves that check to where a fix costs one comment.
- **One round, so the loop ends.** Two agents that disagree can trade
  comments indefinitely. A fixed Claude → Codex → Claude turn always ends in
  a state Hugo can read off the board: `Ready for dev`, or `Requires input`
  with the question named. Claude's `### Response to Codex's review` reply
  under the review is the guard: it is written once, and nothing asks Codex
  again.
- **Claude moves the status, Codex only writes.** Every status move lives in
  one place, the skills, which can be read and changed. Codex needs no write
  access to Linear workflow, and an unreadable reply is still handled: it is
  treated as `Changes requested`.
- **An `@Codex` comment, not delegation.** This was first built on Linear's
  delegate field and changed after the 2026-10-29 test. A delegated issue
  reaches Codex as its title and description, framed as a task to *do*. On
  SNOW-469 that framing beat `AGENTS.md`: Codex implemented the ticket and
  chose a cache lifetime that was Hugo's to choose. The `@Codex` comment makes
  the prompt itself say "scope review only", and it worked first time on both
  test tickets. The worry that held delegation in place, that a mention
  posted through the API under Hugo's account might not trigger Codex, also
  proved unfounded.
- **One scope per ticket, edited in place.** The first version posted a
  `## Scope revision` comment that superseded the original. That left two
  scopes on a ticket for a reader to reconcile. Hugo's rule since is that a
  revision updates the scope it revises. The change log is the first line of
  the scope and Claude's reply under the review.
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
- `implement` builds from the one scoping comment, which revisions edit in
  place.
- Linear's integration runs Codex in the **Codex Cloud (Legacy)**
  environment, not the one GitHub PR review uses. Its setup script must stay
  empty. The stale `./build.sh` script failed the first two runs, and
  `bin/build.sh` is Render's deploy build, which must never run in a review
  container.
- `AGENTS.md` carries the review brief, so the Codex that reviews PRs reads
  the same rules. It points at `CLAUDE.md` rather than copying it.
- The PR carries the agreed scope verbatim (`## Agreed scope`) and a
  `## Deviations from scope` list, so Codex's PR review checks the diff
  against the scope it approved. It is copied rather than linked because the
  PR review cannot be relied on to reach Linear, and a copy is the exact text
  approved, not whatever the ticket says by the time of review. The first
  PR (SNOW-350) matched its scope exactly and drew a clean review. That
  showed no false positive, but not that Codex checks the diff against the
  scope. SNOW-1293's PR settled it: a log line was planted in
  `apps/locations/services/orphans.py`, a file outside the touch list, with
  `## Deviations from scope` reading `None.`, and Codex flagged it as a P1
  finding citing `AGENTS.md`. The line was reverted before merge.
- Skipping the review takes an explicit yes from Hugo, recorded on the ticket.
