---
name: report-churn
description: >
  Re-render the repository's weekly churn chart — commits, releases and lines
  added/removed per ISO week — and republish it to the existing Snowdesk Churn
  Ledger Artifact. Use when the user says "update the churn chart", "refresh
  the churn ledger", "how's throughput been", "chart the churn", or asks how
  the project's output has moved over time. Also used by a weekly Routine —
  when invoked with `routine` (or `weekly` / `--no-approval`) in the args,
  runs end-to-end with no approval gate. Do NOT use to analyse an individual
  PR or diff (that's `code-review`), or to write a Linear status update
  (that's `post-project-update`).
user-invocable: true
allowed-tools: Bash, Read, Artifact
---

# Churn report

One artifact, republished in place: the project's own history charted by ISO
week. It exists because "is this speeding up or slowing down?" is answerable
from git and tedious to answer by hand.

## The one rule that matters

**Never hand-edit the page.** Every figure on it — the totals, the axis
maxima, the prose in "How this was counted" — is computed by
[`bin/render-churn`](../../../bin/render-churn) from `git log`. A number typed
into the HTML is a number that will be wrong next week and will not announce
itself. If the page should say something it does not, change the renderer.

Corollary: the rendered HTML is **never committed**. It is a projection of the
history, and re-running the script is how it is kept current. (`docs/page-audits/`
commits dated files because an audit is authored judgement that cannot be
regenerated; this is the opposite case.)

## The artifact

    https://claude.ai/code/artifact/5652f913-ff71-4168-a4a5-2633f8072884

That URL is the deliverable, and it does not change. Republishing to it keeps
one live page rather than accumulating a dated series — fifty-two churn pages
carry no information the current one lacks.

If the URL is ever lost, find it with `Artifact` `action: "list"` — it is
titled **Snowdesk Churn Ledger** — and correct this file rather than
publishing a second one.

## Steps

1. **Get current history.** The chart is only as fresh as the checkout:

       git fetch origin --quiet && git checkout main && git pull --ff-only

   In a worktree or on a feature branch, `git fetch origin` plus rendering
   from `origin/main` is fine — but say which ref the page reflects.

2. **Render** to a temporary path (never into the repo):

       uv run python bin/render-churn --output /tmp/churn.html

   The script prints the week count and byte size to stderr. A run that
   reports zero weeks means it was run outside the repository — fix that
   rather than publishing an empty page.

3. **Read the existing artifact** before publishing to it:
   `Artifact` `action: "read"` with the URL above. A publish to an artifact
   this conversation has not read is refused, and the read is also how a
   change made elsewhere reaches you.

4. **Republish in place** — `Artifact` with `file_path: /tmp/churn.html` and
   `url:` the URL above. Omit `favicon`; the artifact keeps the one it has.
   Do not pass `title` — the file carries its own.

5. **Report what moved.** Two or three sentences, not a recap of the page:
   the week just closed (commits, releases, net lines), and how it sits
   against the preceding weeks. Name anything genuinely notable — a silent
   week, a deletion-heavy week, a new peak — and say nothing when nothing
   moved. Finish with the artifact URL.

## Modes

### Interactive mode (default)

A human asked. Run steps 1–5 and hand back the URL. No approval gate is
needed: republishing a generated page is reversible and touches nothing else.

### Routine mode

Invoked from the weekly Routine. **Trigger phrases** — any of:

- The invocation args contain `routine`, `weekly`, or `--no-approval`.
- The first user message looks like a scheduled-task header (names a cron
  schedule, starts with `[scheduled]`, or arrives via
  `mcp__scheduled-tasks__*`).

In routine mode:

- **No human is watching.** If step 1 or 2 fails, stop and exit non-zero so
  the runtime surfaces it; do not publish a page rendered from a stale or
  partial checkout.
- **Write only to `/tmp`.** A Routine has no persistent disk, and nothing
  here belongs in the repo anyway.
- **Do not open a PR or touch Linear.** This skill publishes an artifact and
  nothing else. If the renderer itself needs changing, that is a ticket for a
  human, not something to fix mid-routine.

## What this skill does not do

- **Per-author, per-app or per-directory breakdowns.** The chart reports git
  as a whole. Someone asking "who wrote what" is asking a different question
  and should get a different answer, not a fourth panel.
- **Any tie to Linear throughput.** Commits are commits. Ticket counts drift
  from them for legitimate reasons and putting both on one axis invites a
  comparison that does not hold.
- **A quality judgement.** High churn is not bad and low churn is not good.
  Report the shape; leave the reading to the reader.
