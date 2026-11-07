---
name: hugo-signs-off-the-user-scope
description: Why a scope has two parts — user scope (description, Before/After) Hugo signs off; technical scope (scoping comment) Codex reviews
status: current
last-reviewed: 2026-11-07
---

# Hugo signs off the user scope; Codex reviews the technical scope

## Decision

A scope has two parts. The **user scope** is the ticket description: what
changes for the user, before and after, in plain words. Hugo signs it off.
The **technical scope** is the scoping comment, the first comment Claude
posts: Approach, Touch list, Tests. Codex reviews both, once, before the
ticket reaches `Ready for dev`. The contract is in
[`linear-workflow.md`](../linear-workflow.md#scope-contract).

## Why

- **Hugo decides what changes, not how.** The old four-section comment put
  files and tests in front of him for sign-off. That is detail he does not
  need to judge the work, and it buried the part he does: what a user will
  see differently (Hugo, 7 November 2026).
- **Codex is the right reader for the technical part.** It already checks
  touch lists and test layers against the code. It now also checks that the
  technical scope builds every After line and nothing more.
- **Sign-off has a clear edge.** A change to the user scope beyond wording
  goes back to Hugo; a change to the technical scope alone does not. This
  replaces the judgement call "does it alter what is built or why".
- **The description is where a person looks first.** Linear shows it at the
  top of the ticket and in previews, so the part Hugo reads is the part he
  sees.

## Consequences

- `scope` shows Hugo only the user scope in plan mode.
- Open questions live in the user scope. The technical scope has none:
  Claude settles them or states an assumption for Codex.
- The PR's `## Agreed scope` copies both parts, so Codex's PR review checks
  the diff against the After lines as well as the touch list.
- Tickets scoped before this change keep their four-section comment and are
  read as both parts.
- Amends [`codex-reviews-every-scope-once.md`](codex-reviews-every-scope-once.md),
  which still governs the review loop.
