# AGENTS.md — Snowdesk

Instructions for Codex. Codex reads this file; it does not read `CLAUDE.md`,
so this file points there rather than repeating it.

**`CLAUDE.md` is the project's rulebook.** Read it before any review: the
architecture, the model kit, the management-command contract, the three test
layers, the design-system rules and the five invariants all live there, and
every review below is a review against it. Accepted decisions — the "why" —
are in `docs/decisions/`; a review that contradicts one should say so and
name the file.

Codex has two jobs on this repo: reviewing pull requests, and reviewing the
scope of a Linear ticket. It never implements a Linear ticket — Claude Code
does that.

## Review guidelines

For pull request review.

**Review against the agreed scope.** A PR that implements a Linear ticket
carries the scope you reviewed, copied verbatim into the description under
`## Agreed scope`, and a `## Deviations from scope` section. Read both before
the diff, and treat the agreed scope as the specification:

- Everything the scope's Approach and Tests promise is in the diff. A
  promised test that is missing is a finding.
- Nothing in the diff falls outside the scope's touch list unless
  Deviations lists it with a reason. An unlisted deviation is a finding.
  So is a listed one whose reason does not hold.
- The Notes from your scope review were for the implementer; check they
  were heeded.
- A PR with no `## Agreed scope` section (dependency bumps, tooling, work
  with no ticket) is reviewed on the diff alone. A PR that names a ticket
  (`Closes SNOW-NN`) but omits the section should be flagged for it.

Then the usual review:

- Check the diff against `CLAUDE.md` → **Invariants** and the
  **Conventions** sections; a violation of an invariant is always blocking.
- `except A, B:` without parentheses is valid Python 3.14 (PEP 758) and is
  used deliberately. Do not flag it. `CLAUDE.md` → "Known non-issues" lists
  the others.
- A test in the wrong layer (a browser test for something jsdom or the
  Django test client could assert) is a defect, not a style point — see
  `CLAUDE.md` → "Which layer does a test belong in?".
- British English in prose and identifiers we own.

## Scope review (Linear)

You are handed a Linear ticket by **delegation**, in the `Todo` state, after
Claude has posted a scoping comment on it. Your job is to review that scope
**once** and reply. You are the second pair of eyes before implementation,
not the implementer.

**Do not** write code, open a pull request, create a branch, change the
ticket's status, labels or assignee, or open follow-up tickets. Reply with
one comment on the ticket and stop. If you were handed a ticket that has no
scoping comment, reply saying so and stop.

### What to read

1. The ticket's title and description.
2. The **most recent scoping comment** — it has four sections: Approach,
   Touch list, Tests, Open questions (contract in
   `docs/linear-workflow.md` → "Scoping comment contract").
3. The code the touch list names, and whatever it would reach.

### What to check

- **Touch list is real and complete.** Every path exists (or is plainly a
  new file); nothing the approach needs is missing — templates, migrations,
  admin, factories, docs, `docs/glossary.md`, the routing table in
  `CLAUDE.md`.
- **The approach fits the codebase.** It reuses what exists rather than
  adding a parallel path; it respects the decisions in `docs/decisions/`;
  it keeps `apps/ingest` importing no other `apps.*` package.
- **Tests are in the right layer** and cover the change.
- **The project's contracts hold** for anything the scope adds: the model
  kit, the management-command rules, HTMX partials behind `require_htmx`,
  design tokens, i18n, page metadata.
- **One shippable unit.** If the ticket is really two, say where to split it.
- **Open questions.** Answer any you can from the code.

The scope you approve is copied verbatim into the PR, and you will review
the PR against it, so a vague touch list now is a vague check later.

Be specific: name files and functions. A suggestion you cannot tie to the
code or a written rule is an opinion — leave it out. Do not restate the
scope back.

### How to reply

Exactly this shape, so Claude can read it without guessing:

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

- **Approved** means Suggestions and Questions are both empty. Write
  `None.` under each rather than dropping the heading.
- **Changes requested** means at least one Suggestion or Question.
- Notes never block. Use them for things the implementer should know.

There is one round. Claude will answer your review, revise the scope and
move the ticket on — to `Ready for dev`, or to `Requires input` if a
question needs Hugo. You will not be asked again on the same ticket, so put
everything in the one reply.
