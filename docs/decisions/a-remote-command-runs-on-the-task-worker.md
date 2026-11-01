---
name: a-remote-command-runs-on-the-task-worker
description: Why ops MCP run_command queues run_command_task on db_worker, never the request (mcp_runnable, DENIED_OPTIONS, staging refused)
status: current
last-reviewed: 2026-11-01
---

# A remote command runs on the task worker

**Decision.** The ops MCP server's `run_command` never runs a management
command inside the web request. It checks the request, queues one
`run_command_task` (`apps/core/remote_commands.py`) and returns the task id;
production's `db_worker` runs the command under `remote_invocation`, so the
`CommandRun` row is `invoked_via=MCP` with the staff account, the reason and
the task id. Only a command with `mcp_runnable = True` is offered. Where the
task backend runs tasks inline (`ImmediateBackend`: staging, dev, test)
`run_command` refuses.

**Why.** Hugo asked for commands that "return quickly". Run in the request,
a command longer than gunicorn's 30-second timeout is killed part-way and
its register row reads `RUNNING` for ever, and a dry run that walks a table
is easily longer than that. Queueing makes the tool's answer instant
whatever the command does, and the register (SNOW-1189) already records
everything a run needs to be followed and audited.

Staging has no worker, so a queued task there runs inline — the very
failure above. Running dry runs inline only, or everything inline anyway,
were both offered; Hugo chose refusing on 2026-11-01. Adding a worker to
staging is its own infrastructure change.

**Consequences.**

- An opted-in command must finish in minutes: the single worker that runs
  it also sends email and alerts. A long job fans out one task per row
  (CLAUDE.md management-command rule 7) — `relink_bulletin_regions` and
  `recompute_day_ratings` stay shell-only until they do.
- A write is always the explicit `commit: true`; `--commit` in the
  arguments, `--settings`, `--pythonpath` and options naming a server file
  are refused, so the tool cannot reach outside this environment's database.
- One run of a command at a time: a fresh `RUNNING` row or a queued task
  blocks a second.
