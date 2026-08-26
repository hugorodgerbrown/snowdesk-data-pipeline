---
name: bulletin-fidelity-over-simplification
description: Snowdesk renders the provider's bulletin in full and adds alongside it; the replicate-then-subtract pass was retired, not completed
status: current
last-reviewed: 2026-08-26
---

# Fidelity over simplification

## Decision

Snowdesk renders **every field the provider published**, and adds its own
content *alongside* that — never instead of it. A field with no surface on the
page is a defect, not an editorial choice.

The replicate-then-subtract method that governed the bulletin-page design from
April 2026 is **retired rather than completed**. Its Phase 1 (replicate the
canonical SLF/EAWS layout) shipped and stands. Its Phase 2 — a pass to "remove
anything that doesn't earn its place" — is abandoned, and the Phase 3 gate that
depended on it goes with it.

Deciding a field genuinely isn't worth showing is still allowed, but it is a
written, audited act: a row in `tests/sentinels/fidelity.py` carrying an
exclusion reason, reviewable with `bin/fidelity-lint --show-exclusions`.

## Why

Completeness is the product. This is not a simplified bulletin with data
missing — it is the real bulletin, verifiable against the source link, with
Snowdesk adding what the provider cannot: weather at a finer grain than the
warning region, community field observations, and three providers normalised
into one place. A user who follows the source link and finds nothing missing
has a reason to trust the page for a real decision; a competitor shipping a
prettier summary cannot copy that without doing the same work.

The subtraction pass was written against a different premise — Snowdesk as an
*on-ramp* whose job was helping competent intermediates read the bulletin
faster, where anything not serving that goal was a removal candidate. Under
the current premise the two cannot coexist. A document instructing the next
reader to delete provider content, while `bin/fidelity-lint` fails the build
when provider content reaches no surface, is a contradiction the codebase would
resolve in the guard's favour anyway.

The proximate trigger was SNOW-670/671. `metadata.unscheduled` was fetched,
translated, stored and versioned for the project's whole life while no template
read it. That is precisely the failure a subtraction-minded design does not
notice, and it is why the guard exists.

Two of the subtraction pass's open questions survived the retirement, because
they were always *styling* questions — how loud the rating-block header should
be, and whether the aspect rosette is legible on a phone. They moved to the
open-questions list in [`design-system.md`](../design-system.md).

## Consequences

- **Length is managed by hierarchy, not omission.** A long bulletin gets
  collapsible panels, quiet metadata and clear section boundaries. "This
  section is long" is not grounds for dropping it.
- **Three previously-open questions are closed.** The headline band stays on
  simple days; the snowpack and weather prose renders in full; the
  bulletin-level comment is rendered.
- **Dropping WhiteRisk's app chrome is unaffected.** The Close button and the
  Explanation modal belong to WhiteRisk's own application, not to the
  provider's bulletin. Removing them was never subtraction in the sense
  retired here.
- **Additions are no longer gated behind a phase.** Field guidance (SNOW-673),
  the change strip and the serif restyling are independently shippable.
- Reversing this decision means answering the guard: `bin/fidelity-lint` and
  `tests/sentinels/test_fidelity.py` would have to be retired with it.
