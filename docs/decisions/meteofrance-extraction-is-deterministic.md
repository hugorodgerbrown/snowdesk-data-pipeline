---
name: meteofrance-extraction-is-deterministic
description: BRA PDF extraction reads every value off the page — never the wall clock, never fixed geometry where the page states the bound
status: current
last-reviewed: 2026-07-30
---

# BRA extraction reads the page, not the clock or a constant

**Decision.** `scripts/meteofrance-archive/` must derive every value from the
PDF itself. Two specific prohibitions:

1. **No wall-clock reads.** Nothing in the extraction path may call
   `date.today()` or otherwise depend on when it runs. Parsing the same PDF
   twice, a year apart, must give the same record.
2. **No fixed page geometry where the page states the bound.** Section limits
   come from the section's own headings via `find_heading_y`. The
   `BAND_*` constants remain only as a fallback for a page whose headings cannot
   be located.

**Why.** Both rules exist because the original code broke them, and the archive
silently carried the damage for months.

The covered date was derived by reading the year-less line
`"Estimation des risques pour le : SAMEDI 31 JANVIER"` and guessing the year
from `date.today()`, correcting only when the candidate landed ≥180 days ahead.
The backfill ran on 2026-05-21, so a "4 NOVEMBRE" PDF became `2026-11-04` — 167
days ahead, under the threshold — instead of `2025-11-04`. **267 records were
dated a year into the future**, reading as bulletins for a season that had not
happened. Worse, the output depended on the run date, so the archive was not
reproducible: re-running the pipeline a month later would have produced
different dates for the same PDFs. The year was printed on the page all along,
one line above what the extractor read: `"Rédigé le vendredi 30 janvier 2026 à
16h"`.

The stability prose was clipped on both axes. `crop_left` cut it at
`COLUMN_SPLIT_X = 280.0` though the text spans the full page width, and the
fixed `BAND_STABILITY` bottom of 360 pt cut it again though the section grows
with the volume of prose — its closing heading has been observed anywhere from
y=355 to y=400. Between them, **42,672 of 68,237 comment lines (63%) ended
mid-word**, across essentially every record: "Ces plaques se form",
"accumulations d". Nothing failed; the records looked plausible and were merely
half there.

The common thread is that a plausible-looking constant or a convenient ambient
value stood in for something the document stated explicitly. Neither failure
announced itself, which is what made them expensive.

**Consequences.**

- `extract_bulletin_date` takes the redaction datetime as an argument and has no
  default. There is no `_infer_date_with_year` and no reference-date parameter
  to pass a clock into.
- A PDF whose "Rédigé le …" line cannot be parsed is skipped and logged, rather
  than dated by inference. Same for a missing `CreationDate`. Refusing a record
  is preferred over emitting a wrong one — see
  [`meteofrance-bulletin-identity`](meteofrance-bulletin-identity.md).
- `tests/scripts/meteofrance_archive/test_date_inference.py` asserts the
  clock-independence property directly. It previously asserted the *buggy*
  behaviour as correct, including
  `_infer_date_with_year(month=11, day=16, today=2026-05-21) ==
  date(2026, 11, 16)` — one of the 267 bad records, pinned by a passing test.
  A test that encodes a heuristic's output is not a regression test for the
  thing the heuristic was approximating.
- `tests/scripts/meteofrance_archive/test_prose_completeness.py` pins both crop
  bounds, so a reintroduced fixed band or column crop fails there rather than
  quietly shortening every bulletin in the next rebuild.
- Rebuilding the archive is expected to be reproducible. Any diff in a rebuild
  that is not explained by a code change is a bug — see
  [`the rebuild runbook`](../runbooks/rebuild-meteofrance-archive.md).
