---
name: agents-md-is-the-rulebook
description: Why AGENTS.md holds the shared rules and CLAUDE.md imports it (@AGENTS.md), the 32 KiB Codex limit, docs/README.md as the router
status: current
last-reviewed: 2026-11-03
---

# AGENTS.md is the rulebook

**Decision.** `AGENTS.md` holds the rules that bind any coding agent.
`CLAUDE.md` imports it with `@AGENTS.md` and adds only what is specific to Claude
Code. The routing table moved to `docs/README.md` and the per-app architecture
map to `docs/architecture.md`. The rulebook states a rule and links its reason.

## Context

`CLAUDE.md` had grown to 99.6 KB and 968 lines, loaded in every session. The
routing table was 53% of it and the architecture map 16%. Codex read `AGENTS.md`,
which told it to go and read `CLAUDE.md`, so the file most agents depended on
carried one vendor's name.

Hugo set the shape on 3 November 2026: `AGENTS.md` is the default, `CLAUDE.md`
references it, and the rulebook holds rules only.

## Why the shape follows from the tools

- **Codex stops at 32 KiB.** `project_doc_max_bytes` defaults to 32 KiB across the
  instruction files it combines, and later guidance is dropped. The default is set
  in each user's Codex config, so the repo cannot raise it for every environment.
  Hence the limit stated at the top of `AGENTS.md`.
- **Claude Code ignores `AGENTS.md` when a `CLAUDE.md` exists.** The import is what
  loads it. Deleting `CLAUDE.md` would remove the place for Claude-only rules.
- **Codex's PR review reads `## Code Review Rules`** from `AGENTS.md`, so that
  heading and the scope-review brief stay in the file rather than moving to a doc.

## Consequences

- `bin/docs-lint` routes from `docs/README.md`. Its entries keep repo-relative
  text, such as `` `docs/glossary.md` ``, because the check matches that string.
- The ops docs corpus serves `AGENTS` alongside `CLAUDE`.
- Process rules that lived only in Claude's memory directory moved here, where Codex
  and cloud sessions can read them.
- Dated decision records that cite `CLAUDE.md` keep the citation; it was true when
  they were written.
