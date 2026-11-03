---
name: bulk-commands-enqueue-each-row
description: Why a bulk command enqueues one task per row via enqueue_each — READY dedupe, run_after pacing, back_off breaker, mcp_runnable guard
status: current
last-reviewed: 2026-11-03
---

# A bulk command enqueues each row through one helper

**Decision.** A management command whose work is many independent row
updates (a backfill, a rebuild, a clean-up) enqueues one `django_tasks`
task per candidate row through `apps.core.command_tasks.enqueue_each`
and returns (SNOW-1397). The helper streams the candidates, skips a row
whose task is already waiting, paces a task that calls an outside service,
and tallies what it enqueued. The task takes a primary key, re-checks its
row, writes in one transaction and runs at `BULK_TASK_PRIORITY` (−50). A
task that meets a 429 or an outage opens a breaker (`back_off`) that the
tasks after it read (`backing_off`). The command is `mcp_runnable`.

**Why.** Hugo set five rules for these commands on 3 November 2026:
async through the queue, idempotent per row, recoverable after an early
exit, a configurable delay on outside calls, and runnable from the ops
MCP server. SNOW-1345 and SNOW-1346 had each written the enqueue loop by
hand, so a third copy would have been the first of many. One helper holds
the rules in one place, and a guard test holds rule 5.

- **Pacing is `run_after`, not a sleep in the task.** A sleep holds a
  worker thread, and it paces each worker rather than the run: two
  workers halve the gap. Giving task *i* a start of `start + i × pace`
  holds the rate however many workers run, and no worker idles.
  `ImmediateBackend` refuses `run_after` and runs each task as it is
  enqueued, so there the helper sleeps between enqueues instead.
- **Dedupe on waiting tasks.** `run_command` refuses a second run of a
  queued *command*. It cannot see the row tasks a finished command left
  on the queue, so a second `--commit` before they drained would double
  them. Only `READY` rows are matched: a running task may already have
  read its row, and the task's own re-check covers that case.
- **The breaker lives in the cache.** Every worker shares the cache, so
  one task's 429 stops every later task. A skipped row is still a
  candidate, so the next run picks it up.

**Consequences.**

- A bulk command's `--delay` means seconds between task starts.
  `render_ski_tours` used to pause after each walk.
- Each run tallies `already_queued`.
- `enqueue_each` refuses a task above `BULK_TASK_PRIORITY`, and refuses
  an outside task (`calls_outside`) given no `pace`.
- `tests/core/test_command_tasks_guard.py` fails a command that calls
  `enqueue_each` without `mcp_runnable = True`.
- A whole-layer reconcile that is all or nothing by design
  (`import_ski_tours`, `import_rest_zones`, `mirror_reference_data`) is not
  a bulk command. A half-applied layer would tombstone live rows.
