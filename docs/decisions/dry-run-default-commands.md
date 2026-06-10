---
name: dry-run-default-commands
description: Management commands never alter data by default — explicit --commit (preferred) or confirmed --dry-run shapes
status: current
last-reviewed: 2026-06-10
---

# Management commands are read-only by default

**Decision.** A management command invoked with no arguments must not write
to the database, send mail, or call a paid/rate-limited external service.
Two safe shapes are allowed (never mixed in one command): an explicit
`--commit` flag (preferred for new commands), or `--dry-run` plus a
`Proceed? [y/N]` prompt with `--no-input` for unattended runs.

**Why.** These commands run against production data on a schedule; an
operator exploring flags, a mistyped cron line, or an agent probing
behaviour must not be able to mutate state accidentally. Making the
destructive path opt-in turns "ran the wrong command" from an incident
into a no-op.

**Consequences.** The bare invocation is always safe to run for discovery.
Production schedulers must pass `--commit` (or `--no-input`) explicitly.
Full rules and the command catalogue: [management-commands.md](../management-commands.md).
