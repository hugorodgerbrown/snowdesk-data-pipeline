---
name: day-summary
description: day_summary.py, summary_for, classify_movement, classify_readability — the 80-cell copy matrix behind the day-character callout
status: current
last-reviewed: 2026-08-27
---

# The day summary

The one-line sentence beside the day-character label in the callout at the
top of every bulletin page. The label answers *what kind of day is this*;
the summary answers *why, and what does that mean for me*.

Copy and classification live in
[`apps/bulletins/services/day_summary.py`](../apps/bulletins/services/day_summary.py).
`compute_day_character` in
[`render_model.py`](../apps/bulletins/services/render_model.py) derives the
key and calls `summary_for`; the partial is
`templates/includes/day_character_callout.html`.

## What it replaced

Until SNOW day-summary work the explainer was one fixed sentence per
cascade label — five strings for the whole archive. "Persistent or
gliding-snow problems can mask the real risk" appeared on 4,300 pages
whether the day's problem was persistent weak layers or gliding snow,
whether the danger was 2 or 3, and whether it held all day or doubled by
mid-afternoon. It described the *rule that fired*, not the bulletin.

## The three axes

| Axis | Values | Source |
|---|---|---|
| `movement` | `static`, `rising`, `easing`, `shifting` | `classify_movement()` over `compute_period_transition().direction` plus the two windows' problem types |
| `level` | `1`–`5` | Destination level on a changing day, peak level on a static one |
| `readability` | `quiet`, `readable`, `hidden`, `mixed` | `classify_readability()` over every problem type the day names |

4 × 5 × 4 = **80 cells, every one hand-authored**. There is no generic
fallback: an unhandled combination would be the same failure the matrix
exists to fix. `summary_for` clamps an out-of-range level rather than
falling through.

### The transition clause

`rising` and `easing` cells open on `%(transition)s` rather than naming
their levels inline. Direction is ranked on `(level, subdivision)`, so an
SLF day can move without its digit changing — 33 rises and 12 falls in the
archive do, **45 of the 211 changing days, a fifth of them**. Naming both
ends there produces "moderate this morning, moderate by afternoon", which
reads as a rendering bug rather than a subdivision.

`_TRANSITIONS` holds four clauses keyed on `(movement, same_band)`:
"Deteriorating: %(from_word)s this morning, %(to_word)s by afternoon"
when the digit moves, "Deteriorating within %(to_word)s" when it does
not, and the two `easing` equivalents. This is the only composition in
the module — everything after the clause is one authored sentence.

The same trap catches the *tail* of an easing cell: "a lower number" is
false on a subdivision-only fall, so those tails say "an easing rating"
or "the number eases" instead.

### Readability is the load-bearing distinction

It comes straight from the EAWS problem types, and it is the difference
between a day field craft can manage and a day only terrain choice can:

- **readable** — `wind_slab`, `new_snow`, `wet_snow`, `cornices`. The
  problem leaves evidence on the surface. You can go and look at it.
- **hidden** — `persistent_weak_layers`, `gliding_snow`. It does not.
  Glide cracks show *where*, never *when*, which is why gliding snow sits
  here and not with the surface problems — matching `_HARD_TO_READ_PROBLEMS`
  in the cascade.
- **mixed** — both kinds named. The readable half is not the one that sets
  the consequence.
- **quiet** — nothing named, or only `no_distinct_avalanche_problem` /
  `favourable_situation`.

Readability and the `%(problems)s` interpolation are both computed over
**every** problem the day names, morning window included. Classifying on
the afternoon alone let a bulletin render "Hard-to-read day" beside a
sentence that mentioned only wet snow, because the persistent weak layer
ran out at midday. The label and the sentence must be built from the same
set.

## What the archive actually looks like

Census over the 8,080 committed season mirrors
(`apps/bulletins/local_mirrors/*.ndjson`, 27 Aug 2026), replaying
`build_render_model` + `compute_period_transition`. It reproduces the split
counts in
[`docs/research/problem-card-redundancy/`](research/problem-card-redundancy/)
exactly.

| Movement | Bulletins | Share |
|---|---:|---:|
| `static` — nothing changes across the day | 7,791 | 96.4% |
| `rising` — the level climbs | 189 | 2.3% |
| `shifting` — level holds, the problem changes | 78 | 1.0% |
| `easing` — the level falls | 22 | 0.3% |

Three findings shaped the copy:

**A split day is the sun getting to work.** On 254 of the 312 split days
the arriving problem is wet snow. Nothing else comes close.

**Nothing ever improves.** All 22 falling days replace a dry problem —
persistent weak layers, or new snow — with wet snow. The number drops
while the hazard swaps. No archive bulletin has a falling level that
means the snowpack cleared, so the `easing` copy never offers the
afternoon as the safer half, and a test asserts the word "improving"
cannot appear. A day whose danger genuinely receded would be a new case
worth new copy, not this one.

**A quarter of the flat splits are not really splits.** Of the 101
bulletins whose level holds across a split, 78 gain a new problem type,
17 keep the same types on different aspects or elevations, and 6 carry
two identical windows — the split there is an artefact of the provider
stamping one rating with two `validTimePeriod` values.

`classify_movement` compares problem *types*, so only the 78 are
`shifting`; the other 23 are `static`. That is deliberate for the 6, and
a known under-report for the 17: their footprint moves (wet snow
retreating from sunny aspects to below a line, say) and the summary does
not mention it. The trade is one-directional — the sentence omits
something true rather than asserting something false, which is the
failure mode that matters when the reader is deciding where to ski.
Widening it to full problem signatures would need its own copy row, not
a reclassification into the existing one.

This comparison is also why `classify_movement` takes both windows
rather than trusting `direction` alone.

Only 30 of the 80 cells occur in the archive. The other 50 are authored
anyway — provider behaviour is not a contract, and a cell that has never
fired is exactly the one nobody will notice rendering badly.

## Editing the copy

- **One sentence, ending in a full stop.** Tests assert both.
- **Never govern a verb from `%(problems)s`.** It may hold one name or
  four, so "wind slab **is** named" breaks on the second one. Prefer
  "with %(problems)s at the surface", "with %(problems)s in play" — a
  noun phrase, no agreement.
- **A cell may use `%(problems)s` and `%(transition)s`, nothing else.**
  `%(from_word)s` and `%(to_word)s` belong to `_TRANSITIONS`. A test fails
  any cell asking for a placeholder `summary_for` does not supply.
- **Only `rising` and `easing` cells take `%(transition)s`**, and every
  one of them must. Tested in both directions.
- **`quiet` cells must not interpolate `%(problems)s`**, and every other
  cell must. Both directions are tested.
- **An easing tail must survive a same-band fall.** "a lower number" is
  wrong when only the subdivision moved.
- **No two cells share a sentence.** A duplicate means a combination was
  written twice and another was missed.
- **Do not claim an arrival the union cannot support.** "with wind slab
  arriving" is false when wind slab ran all morning. Say what is in play,
  and let `movement` carry the direction.

Bump `DAY_SUMMARY_VERSION` when a cell's copy or the classification
changes, and **re-render the review page** so it does not go stale:

```bash
uv run python bin/render-day-summary-matrix --output docs/research/day-summary-matrix/$(date +%F).html
```

That page sets all 80 sentences side by side with their archive frequency —
the only way to see whether a cell reads as English next to its neighbours.
Every figure on it is computed, so it cannot silently disagree with the
matrix. See [`docs/research/day-summary-matrix/`](research/day-summary-matrix/).

## Related

- [`docs/render-model.md`](render-model.md) — the render model the axes are
  derived from.
- [`docs/day_character_rules_spec.md`](day_character_rules_spec.md) —
  the original five-rule cascade that produces the label (historical).
- [`docs/design-system.md`](design-system.md) — where the callout sits on
  the page and how it is attributed.
