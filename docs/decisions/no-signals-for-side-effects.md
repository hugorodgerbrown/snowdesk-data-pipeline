---
name: no-signals-for-side-effects
description: Save-time side effects (e.g. render model build) are called inline from services, never via post_save signals
status: current
last-reviewed: 2026-06-10
---

# No Django signals for side effects

**Decision.** Side effects triggered at save time — building the render
model, denormalising day ratings — are called inline from the relevant
service function (e.g. `upsert_bulletin()`), never wired via `post_save`
or other Django signals.

**Why.** Signals make data flow invisible: a reader of the service function
can't see what else happens on save, and tests pass or fail depending on
receiver registration order and import side effects. Inline calls keep the
full ingest sequence readable in one place and trivially testable.

**Consequences.** Adding a new save-time side effect means editing the
service function, not registering a receiver. If two entry points need the
same side effect, extract a shared service function and call it from both.
