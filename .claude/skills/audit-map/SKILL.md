---
name: audit-map
description: >-
  Technical review of the map subsystem — static/js/map*.js and its core
  modules, apps/public/api.py's map endpoints, home.html and _map_embed.html,
  static/css/map.css. Re-verifies the previous cycle's findings against the
  code, measures the page, finds what's new, then raises a dated parent ticket
  in Linear with one scoped child per new finding and writes a dated HTML
  artefact at `docs/map-reviews/YYYY-MM-DD.html`. Use when the user says
  "review the map", "map review", "audit the map", or references the map-review
  series. Supports unattended Routine use — when invoked with `routine` (or
  `weekly` / `--no-approval`) in the args, runs end-to-end with no approval
  gate. Do NOT use for the whole-codebase drift audit (that's `audit-code`),
  the product state of the public pages (`audit-pages`), or a per-diff review
  (`code-review`).
user-invocable: true
# Both Linear server names: the local MCP config and the claude.ai connector
# (UUID), which is the only one a remote Routine session sees. See .claude/README.md.
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Artifact, mcp__Linear, mcp__claude_ai_Linear, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8
---

# Map review

A recurring deep review of one subsystem. `audit-code` asks whether the whole
codebase has drifted; `audit-pages` asks whether each page is honest; this asks
whether **the map is well built** — its architecture, failure modes, payload
and testability.

Full conventions, the rating scale, and the two-halves rule for the document:
[`docs/map-reviews/README.md`](../../../docs/map-reviews/README.md).

## The rule that matters

**Verify every finding against the code — never against ticket state, and
never against a doc.** A ticket says what somebody intended to ship. A doc
says what was true when it was written.

Cycle 01 hit both failure modes in the same file. `docs/map-and-api.md` said
`api:region_summary` was "currently unused by the client" — it is not, and
deleting it on that basis would have broken the region panel. The same doc
described `openRegionPopup` as merely "untriggered" when in fact its whole
state machine is unreachable and nine live call sites are permanent no-ops.

Corollary: when a check comes back clean, prove it twice. In this subsystem
specifically, a grep for a bare identifier misses every module that reaches
the same value through `window.snowdeskMapState`, and a grep of `templates/`
misses `{% include %}` chains and context built in views.

## Routine mode

Invoked from the weekly Routine. **Trigger phrases** — any of:

- The invocation args contain `routine`, `weekly`, or `--no-approval`.
- The first user message looks like a scheduled-task header.

In routine mode: skip the approval gate and run end-to-end. No human is
watching, so the discipline matters more, not less — never report a finding
closed without the evidence in hand, never create a ticket you have not
deduped, and if a check is ambiguous say so in the doc rather than resolving
it optimistically.

Exit non-zero on any unrecoverable error (a Linear failure that leaves the
cycle without a parent ticket, a `tox -e docs-lint` failure you cannot fix).

## Step 1 — Establish the baseline

```bash
date -u +%F                                 # cycle date, names the doc + branch
ls docs/map-reviews/*.html | sort | tail -1 # previous cycle
```

Read the previous cycle in full. Its **Findings** and **What this cycle
raised** sections are this cycle's checklist; its masthead records the
baseline commit.

```bash
git fetch origin main
git checkout -B chore/map-review-$(date -u +%F) origin/main
git rev-list --count <previous-baseline>..HEAD
git diff --name-only <previous-baseline>..HEAD -- static/js apps/public docs/map-and-api.md | sort
```

That last diff is the cycle's reading list. A file in it that the explainer
describes needs its claims re-checked.

## Step 2 — Re-take the measurements

The figures strip is re-measured every cycle, not carried forward. A moving
number is often the finding.

```bash
# script count and first-party line/comment split
uv run python - <<'EOF'
import django, os, re
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.development')
django.setup()
from django.test import Client
from django.conf import settings
settings.ALLOWED_HOSTS = ['*']
srcs = re.findall(r'<script[^>]+src="([^"]+)"', Client().get('/').content.decode())
print('scripts:', len(srcs), 'first-party:', len([s for s in srcs if not s.endswith('.min.js')]))
EOF

# server render cost, and per-endpoint size/queries/time (see the previous cycle's table)
# wire cost: gzip -9 each script; then the same again through node_modules/.bin/esbuild --minify
```

Endpoint measurements go through the Django test client with
`settings.DEBUG = True` and `django.db.connection.queries`, not through a
running server — the numbers have to be reproducible in CI and on a laptop.

**Measure production's shape, not the repository's.** `bin/build.sh` runs
`bin/minify-js` in place before `collectstatic`, so raw file sizes in git are
not what anyone is served. Cycle 01 nearly filed "you ship unminified
JavaScript" as a finding; the check that killed it was reading `bin/build.sh`.

## Step 3 — Re-verify every open finding

Work the previous cycle's findings one at a time. Run the check that
originally established it and record the current answer. Shapes that recur:

| Finding kind | How to re-verify |
|---|---|
| Dead code | grep the entry point for call sites; then grep every assignment to the state it owns — a function with no caller is dead, but so is one whose state nothing else ever sets |
| A missing guard | read the construction site, not the handler that would catch it; check whether the handler is defined above or below the thing that throws |
| An uncached endpoint | `assertNumQueries` on a warm second call, not a reading of the decorator |
| Duplicated tables | diff the two symbol sets in Python; "they look the same" and "they are the same" are different findings |
| A doc claim | grep for a consumer of the thing the doc says is unused, across `static/js` **and** `apps/*/templates` |
| A payload claim | measure after `bin/minify-js`, gzipped |

Then pull ticket state from Linear for context — **after** the code checks,
never instead of them. A finding fixed in code whose ticket is open is a
ticket-hygiene note. A finding whose ticket is Done but is unfixed in code is
a real problem and goes in the doc.

## Step 4 — Find what's new

The previous cycle cannot have anticipated the last week. Look for:

- New files in `static/js/` — and whether each is in `MAP_BUNDLE` /
  `home.html` in the right position, or reaches state through
  `window.snowdeskMapState`
- New MapLibre sources and layers (`addSource(` / `addLayer(`) — and whether
  each new layer install ends in `raiseMarkerLayers()`, and whether it
  survives a `setStyle` swap
- New `map.on('sourcedata'` / `'moveend'` listeners — the two events that fire
  hundreds of times per gesture; an unguarded handler on either is a
  performance finding
- New overlays — do they register with `window.pwaMapOverlays`, and do they
  have an owner in the overlay-to-layer tables?
- New endpoints in `apps/public/api.py` — cached like their siblings? in
  `_POSTHOG_EXEMPT_PATHS` if publicly cacheable?
- New `docs/decisions/` entries that supersede something the explainer says

## Step 5 — Draw the consequences

The section that makes this worth reading. Connections neither the tickets nor
the docs can see from inside themselves:

- A finding that has changed character — a carrying cost that has become
  user-visible because a new surface now depends on it
- A guard added this week that makes an older finding moot, or one that was
  added to catch a symptom of a defect class we still have no general defence
  against
- A measurement that has moved in the wrong direction with nobody noticing

## Step 6 — Raise the tickets

**Dedup first.** Search Linear (`list_issues`, Snowdesk team, open states, plus
a keyword query per finding) for an existing open ticket. If one exists, record
the finding as "tracked by SNOW-NN" and do not create a duplicate.

Then, if there is at least one new finding:

1. Create this cycle's **parent** ticket. Title:
   `Map review — YYYY-MM-DD (architecture, dead code, failure modes)`.
   Labels `Chore` + `Map`, priority Medium, **no estimate** — leaves are sized
   and Linear rolls up. State `In Progress`. Body: the findings table with
   each rating and disposition, plus the baseline and the measurements.
2. Create one **child** per finding, parented to it. Apply the trivial /
   spin-off / watching rule from the README — not every finding earns a
   ticket, and saying why in the doc is part of the job.
3. **Scope each child**: post the four-section comment (Approach, Touch list,
   Tests, Open questions) from the `create-ticket` skill, then move to
   `Ready for dev` **only** if Open questions is empty. A child with a real
   open question stays at `Todo` with the question named. Do not manufacture
   "None" to promote a ticket.

If there are **no** new findings, create no parent ticket. Write the cycle doc
recording the re-verifications and say so. A cycle that finds nothing is a
good cycle, not a failed one, and an empty parent ticket is noise.

Ticket-writing rules specific to this series:

- One ticket per independently-shippable unit, even when one finding produces
  two. Cycle 01's "silent failure" finding became a `Map` ticket for the guard
  and a `Platform` ticket for the error reporting.
- Every description must stand alone: file, line, the check that established
  it, and what must **not** be changed alongside it.
- Size with the Fibonacci estimate (1/2/3/5). Priority is an integer enum
  (1 Urgent … 4 Low). See `create-ticket` for the full list of MCP traps.

## Step 7 — Write the doc

Write `docs/map-reviews/YYYY-MM-DD.html`, following
[`docs/map-reviews/README.md`](../../../docs/map-reviews/README.md).

Copy the design system from the previous cycle **verbatim** — the token block,
the type scale, the EAWS finding tiles, the disposition chips. It is
theme-aware in all three states (bare `:root`, `prefers-color-scheme`,
`[data-theme]`); do not re-derive it, and never define a colour only inside a
media or `[data-theme]` block.

Carry the explainer (sections 01–07) forward and **correct** it against this
cycle's diff; rewrite the review (08–11) as Closed / Unchanged / New. Re-take
every number in the figures strip.

Then update the "Most recent cycle" pointer and the History table in
`docs/map-reviews/README.md`.

## Step 8 — Ship

1. Apply any inline fixes the trivial rule allows. Keep them genuinely trivial.
2. `uv run tox -e docs-lint` — run it, don't assume. If the cycle touched
   anything else, run the envs that cover it.
3. Publish the doc as an Artifact (new file path each cycle → new URL) and put
   the URL in the doc's History row and the PR body.
4. Commit with `--author="Claude <noreply@anthropic.com>"`, subject
   `SNOW-NNN: map review YYYY-MM-DD`.
5. Push and open a **draft** PR whose body starts with `Closes SNOW-NNN`,
   carries the Artifact URL, the findings table and the child-ticket list, so
   the PR is readable without opening the file.
6. Move the parent ticket to `In Review` and post the PR URL as a comment.

## Do not

- **Fix the findings.** This cycle reports and tickets; it does not implement.
  The one exception is the trivial inline fix, and "trivial" is the README's
  definition, not a judgement call in the moment.
- **Ticket a finding whose own recommendation is "do it opportunistically".**
  Record it as watching and re-measure next cycle.
- **Restate the subsystem from scratch.** The explainer is carried forward and
  corrected. A cycle whose review half reads the same as last week's has
  failed, even if every word in it is true.
- **Trust the previous cycle's numbers.** Re-measure. A number that has moved
  is often the most useful thing in the document.
