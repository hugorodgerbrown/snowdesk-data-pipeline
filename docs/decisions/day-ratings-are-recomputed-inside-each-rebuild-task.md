---
name: day-ratings-are-recomputed-inside-each-rebuild-task
description: rebuild_render_models / rebuild_source_render_models enqueue a task per bulletin that locks and recomputes its day ratings
status: current
last-reviewed: 2026-11-01
---

# Day ratings are recomputed inside each rebuild task

## Decision

`rebuild_render_models --commit` and `rebuild_source_render_models
--commit` only enqueue: one task per stale bulletin
(`apps/bulletins/services/render_rebuild.py`), at priority -50, plus one
per stale `SourceDayRating` on the service side. Each bulletin task
rebuilds its row **and** recomputes the day rating of every (region, day)
that row governs, in one `transaction.atomic()`. Before reading the
pair's candidates it locks the pair's rating row (`select_for_update`,
ordered by region and day, a missing row created first). A build error
commits the version-0 sentinel, recomputes nothing, and fails the task.

## Why

- **CLAUDE.md command rule 7.** Each command walked every bulletin inline
  — thousands of rows, tens of thousands a season once SNOW-998's
  per-region bulletins land — and refreshed the ratings at the end, so a
  dropped shell lost the lot. `render_ski_tours` (SNOW-1345) is the shape.
- **The ordering that matters is inside a command, not between them.** A
  rating has to be computed after the bulletins that govern it. With one
  task per bulletin there is no "end of the walk" to recompute at, so the
  recompute moves into the task. Rebuilding the row and its ratings in one
  transaction means a recompute that fails rolls the row back, and the
  row stays a candidate for the next run, rather than being marked
  current with its ratings stale and nothing left to find it.
- **Sibling bulletins share a pair.** The morning and prior-evening issues
  both target one day; their tasks can run in either order, or at once on
  two workers. The lock serialises them: the second task waits, then reads
  candidates after the first has committed, so the governing bulletin's
  rating wins whatever the order. Sorting the locks means two tasks that
  share several pairs cannot deadlock.
- **The two Deploy-Steps are independent.** The instance rebuild builds
  from the service's stored CAAML (`SourceBulletin.caaml`, since SNOW-1337
  the only copy) and never reads the service's rebuilt model, and the two
  write different tables. They keep their documented order as
  convention only.

## Consequences

- On production's `DatabaseBackend` a `--commit` run returns in seconds;
  completion is read by re-running the preview, which counts the rows
  still stale. A failure shows on the task result and the worker log.
  Under `ImmediateBackend` (dev, test, staging) each task runs inline and
  a FAILED result counts towards the command's non-zero exit.
- A task re-reads its row and skips one that is gone or already current,
  so a duplicate enqueue writes nothing and moves no `updated_at`.
  `--all` (and `--bulletin-id` on the instance) pass `force`.
- **The bulletin row is locked `FOR NO KEY UPDATE`**
  (`select_for_update(no_key=True)`), never plain `FOR UPDATE`. A
  sibling's recompute may set a shared rating's `source_bulletin` to this
  bulletin, and Postgres checks that foreign key with a `KEY SHARE` lock
  on the bulletin row. `FOR UPDATE` blocks `KEY SHARE`, so a forced
  rebuild of a morning and an evening issue could deadlock: the morning
  task holding its row and waiting on the rating, the evening task holding
  the rating and waiting on the morning row. `FOR NO KEY UPDATE` does not
  conflict with `KEY SHARE`. The rating rows keep plain `FOR UPDATE`.
- The `@task` wrappers live in `apps/bulletins`, because `apps/ingest`
  imports no other `apps.*` package; ingest contributes only
  `lock_source_day_ratings`, through `apps.ingest.public`.
- `rebuild_track_renders` stays inline: in-memory work, rarely deployed,
  no measured cost.
