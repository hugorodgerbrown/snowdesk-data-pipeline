# AGENTS.md — Snowdesk

The project's rulebook, for every coding agent. Claude Code imports this file
from `CLAUDE.md`, which adds only what is specific to Claude. Keep this file
under 32 KiB: Codex stops reading there by default
([why](docs/decisions/agents-md-is-the-rulebook.md)).

Django pipeline that fetches avalanche bulletins from SLF, ALBINA (EUREGIO)
and Météo-France, normalises them to CAAML v6 JSON, stores them and renders
them on a public site. The frontend uses HTMX and no JavaScript framework.
Python 3.14 / Django 6.1, pinned in `pyproject.toml`.

**Finding things:**

- [`docs/README.md`](docs/README.md) — every doc and decision record, by topic
  and code symbol. Search it before changing an area, and read what it names.
- [`docs/architecture.md`](docs/architecture.md) — diagrams of how it runs and
  how the code is layered, then what each app owns.
- [`docs/glossary.md`](docs/glossary.md) — domain term → code symbol.
- [`docs/decisions/`](docs/decisions/) — accepted decisions, the "why".
- [`apps/bulletins/CLAUDE.md`](apps/bulletins/CLAUDE.md) — per-provider APIs,
  auth and payloads, for any agent.

## Boundaries

- `apps/ingest` imports no other `apps.*` package, and everything else imports
  it only through `apps/ingest/public.py`. `tests/ingest/test_boundary.py`
  enforces both. No extraction is planned; the boundary is kept so that one
  stays possible ([why](docs/decisions/ingestion-is-a-rehostable-unit.md)).
- `track/` imports no Django and no `apps.*`
  ([why](docs/decisions/pure-track-maths-is-a-package.md)).
- `apps/bulletins` and `apps/regions` split by data lifecycle
  ([why](docs/decisions/bulletins-regions-split.md)).

## Running locally

```bash
cp .env.example .env          # fill in values
uv sync
npm install
uv run python manage.py migrate

npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch
uv run python manage.py runserver
mailpit                       # local SMTP sink, inbox at http://localhost:8025
```

Tests never reach Mailpit: assert against `mail.outbox`.

## Dependency management

- Use **uv** (`uv add`, `uv add --dev`, `uv lock --upgrade`). `pyproject.toml`
  is the source of truth; commit `uv.lock` with every dependency change. There
  is no `requirements.txt`.
- The virtualenv stays at `.venv/` in the repo
  ([why](docs/decisions/in-project-venv.md)).
- Every tox env installs from `uv.lock`, so `tox.ini` needs no edit for a
  runtime dependency ([why](docs/decisions/tox-envs-install-from-uv-lock.md)).
- A new tool for a tox env goes in the matching `[dependency-groups]` group
  (`test`, `type`, `lint`, `sast`, `offline`, `e2e`), then run `uv lock`.

## Conventions

### Code

- **Header comment block** on every module; **docstring** on every function
  and class.
- All function arguments are typed, except `*args` and `**kwargs`.
- `ruff` lints and formats; `pre-commit` enforces it. No `# noqa` without a
  comment giving the reason.
- British English (colour, behaviour, organise), except third-party
  identifiers.
- Favour passing service objects as arguments over class hierarchies.
- No abstraction until at least two callers need it.
- Settings: `config/settings/base.py` plus the `development`, `production`,
  `staging` and `perf` overlays, chosen by `DJANGO_SETTINGS_MODULE`.
- Secrets come through `python-decouple`.
- `logging.getLogger(__name__)` in every module; configuration is `LOGGING` in
  `base.py`.
- **No Django signals for side effects.** Call them inline from the service
  function ([why](docs/decisions/no-signals-for-side-effects.md)).

#### Known non-issues — do not flag

- `except A, B:` without parentheses is valid Python 3.14
  ([PEP 758](https://peps.python.org/pep-0758/)) and used deliberately. A
  `SyntaxError` from that line means the system Python ran; use `uv run python`.

### Models

Every concrete model has: `BaseModel` as its base; an explicit admin class; a
`to_string()` method that `__str__` delegates to; an explicit `Meta.ordering`
(`-created_at` by default); a custom queryset; a test factory and test coverage.

### Testing

- pytest + FactoryBoy, in `tests/` mirroring the source tree, one
  `test_{module_name}.py` per module.
- All new code has covering tests. The coverage target is 90%.
- Run the suite with `uv run tox -e test`. `uv run pytest <path>` is fine while
  iterating. Never a bare `pytest`.
- CI on a PR runs only the tests the diff can affect; `main` and `release` run
  everything ([why](docs/decisions/a-pr-runs-the-tests-its-diff-can-affect.md)).
  A local `--testmon` run needs `COVERAGE_CORE=ctrace`.
- All datetimes have `tzinfo`.
- Call factories with `.create()`, never `RegionFactory(...)`, so mypy infers
  the model type.

#### Which layer does a test belong in?

Stop at the first layer that can hold the assertion. A test in the wrong layer
is a defect, whatever it asserts.

1. **pytest** (`tests/`) — anything the Django test client can reach: status
   codes, redirects, rendered HTML, HTMX fragments, querysets, services,
   commands.
2. **Vitest** (`tests/js/`) — anything a `static/js/` module does that jsdom can
   observe. The default home for client-side behaviour.
3. **Playwright** (`tests/e2e/`) — only what needs a real browser: MapLibre's
   WebGL canvas, the service worker, real clipboard or WebAuthn ceremonies,
   multi-script journeys.

`tests/e2e/` stays at roughly a dozen tests: one file per user journey, each
test under 40 lines, each mapped to a scenario in
[`docs/testing-scenarios.md`](docs/testing-scenarios.md) by a `Scenario:` line
(or `Scenario: none — <reason>`). `tox -e e2e-lint` enforces this. `MAX_TESTS`
is a backstop, not a target; raising it takes a ticket that says why. Rules and
history: [`docs/client-side-tests.md`](docs/client-side-tests.md).

## Management command design

Every new or refactored command (full contract and catalogue:
[`docs/management-commands.md`](docs/management-commands.md)):

1. **Runs with no arguments**, doing the most useful thing for the common case.
2. **Never alters data by default.** Either an explicit `--commit` (preferred)
   or `--dry-run` + `Proceed? [y/N]` with `--no-input`, never both
   ([why](docs/decisions/dry-run-default-commands.md)).
3. **Respects `--verbosity`** in log calls.
4. **Exits non-zero on failure**, including `records_failed > 0`.
5. **Streams a growable queryset.** Order `-id` and iterate with
   `apps.core.command_iteration.iterate_rows` (or `countdown` for a derived unit
   of work), printing each row's id and a readable label. `describe` is required
   and reads only loaded fields. State kept across rows holds keys or slim
   `.only()` instances. An exemption needs an inline reason.
6. **Inherits `RegisteredCommand`, not `BaseCommand`**, and calls
   `self.tally(name, n)` for what it changes or would change, zeros included
   ([why](docs/decisions/every-command-run-is-registered.md)).
   `mcp_runnable = True` only for a command that finishes in minutes
   ([why](docs/decisions/a-remote-command-runs-on-the-task-worker.md)).
7. **Long work made of independent updates goes on the task runner**, through
   `apps.core.command_tasks.enqueue_each`. The task takes a pk, re-checks its
   row, writes in one transaction and runs at `BULK_TASK_PRIORITY`; a task that
   calls out registers `calls_outside` and uses `wait_for_slot` and `back_off`.
   The command is `mcp_runnable`
   ([why](docs/decisions/bulk-commands-enqueue-each-row.md)).

## Frontend

**Tailwind CSS v4.** Source `src/css/main.css` (`@theme` tokens and component
exceptions), output `static/css/output.css` (gitignored). Style with utility
classes; add custom CSS only for what Tailwind cannot express.

**Page metadata.** Every template extending `public/base.html` overrides
`{% block page_meta %}` with one
`{% include "includes/_page_meta.html" with title=… description=… %}`. A page
that should never unfurl passes `sharing=False` and a comment saying why
([why](docs/decisions/page-metadata-is-explicit-or-opted-out.md)).

**HTMX.**

- Full-page views return a complete response. Fragment views return the inner
  HTML, are routed under the owning app's `urls.py` with a `partials/` prefix,
  and are guarded by `require_htmx`.
- Use `hx-target`, `hx-swap="innerHTML"` and `hx-indicator` for dynamic requests.
- `public/base.html` does not load htmx. A page that renders any `hx-*`
  attribute loads `js/htmx.min.js` in its own `extra_js` block, and a new
  fragment-posting page joins the list in `tests/public/test_htmx_is_loaded.py`.

## Design system

The reference is the staff-only component library at `/_components/` (source
`apps/design_system/design_tokens.py`; fixtures `apps/design_system/_component_fixtures.py`).
Read it before adding a visual surface. `DESIGN.md` and the
[external design system](https://claude.ai/artifact/6n3bKzXkQfr5QsANFDjPUq) are
synced copies; `src/css/main.css` is the source of truth for tokens.

`bin/ds-lint` enforces rules 1–3 on templates and `static/js/*.js`:

1. **Reuse first, extract second, inline never.** Use an existing partial. If
   the shape exists inline elsewhere, extract a partial with a registry entry.
2. **Design tokens, not raw Tailwind utilities.** `bg-card`, `text-text-1/2/3`,
   `border-border`, `bg-status-*`; `rounded-card`/`-tag`/`-pill`/`-sm`;
   `text-meta`/`-chip`/`-caption`/`-summary`/`-label` and `leading-prose`.
   Primary CTAs use `templates/includes/_button.html`.
3. **Hex colours belong in `@theme`.** In templates the only exceptions are SVG
   `fill`/`stroke` and the `theme-color` meta tag. MapLibre paint properties and
   the EAWS danger palette in JS are exempt from this rule only.

No linter holds rules 4–7; reviewers do
([statement and history](docs/design-system.md#interaction-principles)):

4. **Put things where the user expects them.** A new menu adopts an existing
   order and drops what does not apply. Routes row: Plan a trip → Share → Rename
   → Delete. Favourite row: Share → Rename → Remove. Destructive is last.
5. **In a restricted area, one CTA is a bare icon; two or more collapse into a
   "…" menu.** A restricted area is a map overlay, sheet or panel row.
6. **What opened a map panel decides its kind**, all on `_overlay_sheet.html`:
   an **object sheet** (a thing tapped on the map; peek/half/full on phones), a
   **roundel panel** (opened by a roundel; no detents) or a **form panel** (a
   task; no detents, no drag-to-close).
7. **A surface opens beside its trigger.** On a desktop a selection docks left
   under the search pill (`side="left"`); on a phone it slides up. Right-hand
   roundels open on the right. One object sheet at a time; the date row stays
   put; a sheet draws no focus ring round itself.

Per-line escape, with a reason a reviewer can judge cold
(`bin/ds-lint --show-allows` audits them):

```html
{# ds-lint-allow: <reason> #}
```

```js
// ds-lint-allow: <reason>
```

**Other guards on `static/js` and content:**

- `tox -e js-globals-lint` — fails on a `window.X` / `self.X` read nothing
  assigns. Top-level `function` and `var` become window properties; `let`,
  `const` and `class` do not. Real externals go in `KNOWN_EXTERNALS`.
- `tox -e i18n-lint` — fails on a user-facing literal reaching `textContent`,
  `innerHTML`, `aria-label` and the like. Render it in the surface partial's
  strings `<template>` and read it with `window.pwaStrings.read()`. The hatch
  `// i18n-allow: <reason>` is for staff-only surfaces. See
  [`docs/i18n.md`](docs/i18n.md).
- `tox -e js-types` — `tsc --checkJs` over files with `// @ts-check`, typed in
  JSDoc. CI-only. `grep -L '^// @ts-check' static/js/*_core.js` lists what is
  still unchecked. `static/js/globals.d.ts` declares the `window.pwa*` channel.
- `tox -e fidelity-lint` — every CAAML path in the sentinels is declared rendered
  or excluded with a reason in `tests/sentinels/fidelity.py`
  ([contract](tests/sentinels/README.md)).

## Local CI

`tox` is the entry point for linters, type checks, Django checks and tests.

```bash
uv run tox                    # the default envlist in tox.ini
uv run tox -e test            # one env; also lint, fmt, mypy, django-checks,
                              # ds-lint, docs-lint, migrations-lint, js, …
uv run tox -e sast            # required in CI, not in the default envlist
uv run tox -e audit           # required in CI, not in the default envlist
uv run tox --recreate         # after a dependency change, if envs behave oddly
```

- **Before opening a PR**, run `uv run tox` and fix every failure.
- A follow-up commit on an open PR runs only the tests it touches; CI covers
  the rest.
- `tox -e e2e` and `npm run lh` belong to CI. Do not run them in a local
  implement or review loop.
- After editing a template, run `pre-commit run djangofmt --files <path>`.

## Workflow

Linear (team prefix `SNOW-`) tracks the work. Full lifecycle, scoping-comment
contract and PR body template:
[`docs/linear-workflow.md`](docs/linear-workflow.md).

- **Status.** `Backlog` → `Todo` (scoped) → `Ready for dev` → `In Progress`
  (branch created, moved by hand) → `In Review` (PR opened) → `Done` (merged).
  `Requires input` holds a question only Hugo can answer.
- **Scope.** Two parts
  ([contract](docs/linear-workflow.md#scope-contract),
  [why](docs/decisions/hugo-signs-off-the-user-scope.md)). The **user scope**
  is the ticket description: what changes for the user, before and after, in
  plain words. Hugo signs it off. The **technical scope** is the scoping
  comment, the first comment Claude posts: Approach, Touch list, Tests. Codex
  reviews both.
- **Scope review.** Codex reviews every scope of size S and above once.
  An XS scope with no open questions goes straight to `Ready for dev`. An
  unsized ticket counts as S
  ([why](docs/decisions/codex-reviews-every-scope-once.md)). If Codex's review
  changes the user scope beyond wording, it goes back to Hugo; a change to the
  technical scope alone does not.
- **Implementation plan.** Hugo reviews the user scope, not the technical scope
  or the plan. Once he has signed it off and Codex has reviewed both parts,
  Claude writes the plan and builds from it with no approval stop.
- **When a ticket may be skipped.** A change that touches no model, migration,
  URL, command, dependency or stored data shape needs no ticket, scope or
  `SNOW-` prefix, whatever its line count. Copy, styling, map paint, docs and
  test fixes qualify. Everything else has a ticket.
- **Branches.** `feature/SNOW-xxx-short-description`, `fix/…`, `chore/…`; one
  ticket per branch. Never commit to `main`.
- **Commits and PRs.** Subject prefix `SNOW-xxx:`. PR title
  `SNOW-42: short imperative summary`; the body starts with `Closes SNOW-42`.
- **Fix what you find.** A defect found while working is fixed in the same PR and
  listed under Deviations from scope, not raised as a follow-up ticket or left in
  a note. A sweep far larger than the change it sits in is a question for Hugo.
  Design changes nobody asked for are also a question for Hugo.
- **Merging.** A PR needs no human approval, however it was opened. It squash-merges once every required
  check is green and Codex's one PR review has arrived and is resolved: a 👍, or
  each comment verified, fixed or answered, and resolved. Do not use
  `gh pr merge --auto`, which merges before Codex has reported.
- **Stop and ask** when either part of the scope is missing, a test failure has no
  obvious fix, or the work shows the scope was wrong (comment on the ticket first).

## Path to live

`main` deploys to staging; `release` deploys to production. Before opening a
release PR, run the staging check in
[`docs/deployment.md`](docs/deployment.md#cutting-a-release) and report it. Hugo
decides whether to ship.

Run `bin/cleanup-merged-branch <branch>` and `bin/sync-with-origin` (dry run by
default) from the primary worktree, on `main`, to clear merged branches.

## Invariants

These hold at all times. Any violation blocks a PR.

1. **No `mark_safe()` on user-supplied content.**
2. **Email addresses are lowercased** before storage and lookup, at every entry
   point.
3. **Subscription emails are always async.** Dispatch with `django_tasks.task`
   and `.enqueue()`. No direct `send_mail()` on the request path. Staging uses
   `ImmediateBackend` because it has no `db_worker`.
4. **HTMX partial views are guarded by `require_htmx`** and reject a plain
   request with a 400.
5. **No secrets in source.** Credentials come through `python-decouple`; `.env`
   is gitignored.

## Documentation

- Every doc under `docs/` has frontmatter (`name`, `description`,
  `status: current|draft|historical`, `last-reviewed`) and is reachable from
  [`docs/README.md`](docs/README.md). `tox -e docs-lint` enforces both. Front-load
  the `description` with the nouns an agent would search for. `code-reviews/`,
  `map-reviews/`, `page-audits/`, `qa/` and `research/` hold dated artefacts and
  are exempt.
- A non-obvious architectural choice gets a file in `docs/decisions/`. A domain
  term that gains a code symbol gets a line in `docs/glossary.md`.
- This file states rules and links to their reasons. History and narrative go in
  the decision record or doc it links.

## Code Review Rules

For Codex, reviewing a pull request.

**Review against the agreed scope.** A PR that implements a Linear ticket
carries both parts of its scope, copied verbatim into the description under `## Agreed scope`,
and a `## Deviations from scope` section. You reviewed that scope unless the
ticket is XS. Read both before the diff, and treat the agreed scope as the
specification:

- Everything the user scope's After lines and the technical scope's Approach
  and Tests promise is in the diff. A promised test that is missing is a finding.
- Nothing in the diff falls outside the touch list unless Deviations lists it
  with a reason. An unlisted deviation is a finding, and so is a listed one whose
  reason does not hold. A defect fixed under "Fix what you find" is a valid reason.
- The Notes from your scope review were for the implementer; check they were
  heeded.
- A PR with no `## Agreed scope` (dependency bumps, tooling, a change that may
  skip a ticket) is reviewed on the diff alone. A PR that names a ticket
  (`Closes SNOW-NN`) but omits the section should be flagged for it.

Then the usual review:

- A violation of an invariant above is always blocking. Check **Conventions**
  too, and respect **Known non-issues**.
- A test in the wrong layer is a defect, not a style point.
- A review that contradicts a file in `docs/decisions/` says so and names it.
- British English in prose and identifiers we own.

## Scope review (Linear)

For Codex. You are asked for a review by an **`@Codex` comment** on a Linear
ticket in the `Todo` state, after Claude has posted a scoping comment. The comment
opens "This is a **scope review only**". Review that scope **once** and reply. You
are the second pair of eyes before implementation, not the implementer.

If a Linear issue is **delegated** to you, or you are mentioned without that
review request, do not implement it. Reply that on this repository you only review
scopes and pull requests, and stop. Claude Code implements every ticket.

**Do not** write code, open a pull request, create a branch, change the ticket's
status, labels or assignee, or open follow-up tickets. Reply with one comment and
stop. If the ticket has no scoping comment, say so and stop.

### What to read

1. The ticket's title and description.
2. **The scope**, in two parts (`docs/linear-workflow.md` → "Scope contract"):
   the **user scope** in the description (Summary, Before, After, Not changing,
   Open questions), which Hugo has signed off, and the **technical scope** in the
   scoping comment (Approach, Touch list, Tests). A ticket scoped before
   7 November 2026 has one comment holding Approach, Touch list, Tests and Open
   questions.
3. The code the touch list names, and whatever it would reach.

### What to check

- **The two parts agree.** The technical scope builds and tests every After line,
  and nothing the user scope does not promise. Each After line is something a
  user could check. Say when a suggestion changes the user scope: it goes back
  to Hugo.
- **Touch list is real and complete.** Every path exists or is plainly new; nothing
  the approach needs is missing: templates, migrations, admin, factories, docs,
  `docs/glossary.md`, `docs/README.md`.
- **The approach fits the codebase.** It reuses what exists, respects
  `docs/decisions/` and keeps the **Boundaries** above.
- **Tests are in the right layer** and cover the change.
- **The project's contracts hold**: the model kit, the command rules, `require_htmx`,
  design tokens, i18n, page metadata.
- **One shippable unit.** If the ticket is really two, say where to split it.
- **Open questions.** Answer any you can from the code.

The scope you approve is copied verbatim into the PR, and you will review the PR
against it, so a vague touch list now is a vague check later.

Name files and functions. A suggestion you cannot tie to the code or a written rule
is an opinion; leave it out. Do not restate the scope.

### How to reply

Exactly this shape:

```markdown
## Codex scope review

**Verdict:** Approved | Changes requested

### Suggestions
1. <what to change in the scope, and why — file or rule cited>

### Questions
1. <what the scope does not settle, and why it matters>

### Notes
- <non-blocking observations for whoever implements it>
```

- **Approved** means Suggestions and Questions are both empty. Write `None.` under
  each rather than dropping the heading.
- **Changes requested** means at least one Suggestion or Question.
- Notes never block.

There is one round. Claude answers your review, revises the scope and moves the
ticket to `Ready for dev`, or to `Requires input` if a question needs Hugo. You will
not be asked again on the same ticket.
