---
name: merge-prs
description: |
  Land a batch of pull requests one at a time, keeping each one green:
  order the batch, sync every PR onto the advancing `main`, resolve merge
  conflicts, block on CI, and squash-merge. Use when the user says "merge
  these PRs", "land PRs #12 #15 #18", "clear the merge backlog", "merge the
  batch", or gives a list of PRs to get in. Do NOT use for opening a single
  PR from a ticket (that is the `implement` skill), or for fast-forwarding
  `release` to production (that is the `release` skill).
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion
---

# Merge a batch of PRs

Drive a list of pull requests to **merged**, sequentially, keeping each one
green as `main` moves underneath it. Every merge advances `main`, which
invalidates the mergeability and CI verdict of every PR still in the queue —
so this is a strict one-at-a-time loop, not a fan-out. The unit of progress
is "one PR fully landed"; the loop only starts the next PR once the current
one is merged.

## What this skill owns vs. what CI owns

- **Sequencing, syncing, conflict resolution, and the merge call** are
  yours. You decide the order, bring each PR up to date with `main`, resolve
  conflicts, and issue the squash-merge.
- **The pass/fail verdict is CI's.** Required checks are enforced by the
  repository **ruleset** ("Main branch rules", id `19141641`), not classic
  branch protection — GitHub will reject a merge whose required checks are
  not green on the head commit. This skill **waits** for that verdict; it
  never merges around it with admin override.
- **Signed commits are the ruleset's, not yours to waive.** The same ruleset
  carries a `required_signatures` rule: every commit reachable in the PR must
  have a **verified** signature or `gh pr merge` is rejected with *"the base
  branch policy prohibits the merge"* — even with all checks green and zero
  required reviews. This skill **re-signs** unsigned commits (step 4d); it
  never bypasses the rule with `--admin`.

## Preconditions

Run from the **primary worktree**, on `main`, with a clean tree.

```bash
git rev-parse --show-toplevel
git branch --show-current          # must be main
git status --porcelain             # must be empty
git fetch origin --prune --quiet
git pull --ff-only
```

If the branch is not `main` or the tree is dirty, stop and tell the user to
land or stash their work first. Do not stash on their behalf.

**Signing must work**, because `required_signatures` blocks unsigned commits
(step 4d re-signs them):

```bash
git config --get commit.gpgsign     # expect true
git config --get user.signingkey    # must be set — if empty, signing fails silently
printf t | perl -e 'alarm 8; exec @ARGV' gpg --batch -u "$(git config --get user.signingkey)" -o /dev/null -s -
```

If `user.signingkey` is unset or the test sign hangs/fails (a passphrase the
agent hasn't cached), stop and ask the user to configure signing — do not
guess a key. (`timeout` is absent on macOS; the `perl -e 'alarm'` wrapper
caps the sign so a pinentry prompt can't hang the run.)

**All PR-branch work happens in throwaway detached worktrees** under the
scratchpad — sync/sign (4d) and conflict resolution (step 5) both
`git worktree add --detach <scratch> origin/<headRef>`, act, push, then
remove. This leaves the user's own checked-out worktrees untouched, so a PR
branch that is live in another worktree is not a blocker. **Always `cd` back
to the primary worktree before `git worktree remove`** — removing the shell's
cwd worktree breaks it with *"Unable to read current working directory"*.

## Step 1 — Resolve the batch

Determine which PRs are in scope:

- **Explicit list** (`merge #12 #15 #18`, or a Linear/PR URL list) — use
  exactly those, in the order given unless the user asks to reorder.
- **No arguments** — list the open, non-draft PRs and treat that as the
  candidate batch, oldest-first:

  ```bash
  gh pr list --state open --draft=false \
      --json number,title,isDraft,createdAt \
      --jq 'sort_by(.createdAt) | .[] | "\(.number)\t\(.title)"'
  ```

Fetch the current state of every PR in the batch in one pass:

```bash
gh pr view <pr> --json number,title,state,isDraft,mergeable,\
mergeStateStatus,reviewDecision,baseRefName,headRefName,labels
```

Drop from the batch (and report) any PR that is not landable and never will
be by this skill:

- `state` is not `OPEN` (already merged or closed).
- `isDraft` is true — a draft is not ready; do not un-draft it.
- `baseRefName` is not `main` — this skill only lands onto `main`. Surface
  stacked/release-targeted PRs for the user to handle separately.

## Step 2 — Order the queue

Order matters because each merge rebases the problem for the rest.

1. **Respect stated dependencies.** If the user says "15 depends on 12", or a
   PR body says "stacked on #12" / "merge after #12", that constraint wins.
2. **Otherwise oldest-first** (by `createdAt`) — the default that minimises
   how far behind the others drift.
3. **Float trivially-clean, low-conflict PRs earlier** only if it reduces
   total conflict work and violates no dependency. Do not over-optimise;
   oldest-first is a fine default.

## Step 3 — Confirm the batch (hard gate)

Merging is outward-facing and irreversible in effect — each merge to `main`
auto-deploys staging on Render and can auto-close a Linear ticket via the
PR's `Closes SNOW-xx` line. Confirm before anything moves.

Present the ordered queue as a table and state the plan explicitly:

```markdown
## Ready to merge — in this order

| # | PR | Title | State | Notes |
|---|----|-------|-------|-------|
| 1 | #12 | SNOW-40: … | CLEAN | ready |
| 2 | #15 | SNOW-41: … | BEHIND | will sync onto main, re-run CI |
| 3 | #18 | SNOW-42: … | DIRTY | merge conflict — will resolve, may pause |
```

Then, via `AskUserQuestion`, confirm:

- the **merge method** — default **squash** (repo convention: keeps
  `SNOW-xx` in the git log after merge). Only offer merge-commit/rebase if
  the user asks.
- whether to **delete each head branch** after merge (default yes).
- **verification depth** (see step 4): **safe** (re-sync + re-test every PR
  against the `main` it will actually merge into — default) or **fast**
  (only sync/test PRs GitHub reports as `BEHIND`/`DIRTY`; trust an already-
  green `CLEAN` PR even though earlier merges moved `main`).

Also state plainly that landing these will **force-push** each PR branch:
re-signing unsigned commits and rebasing onto `main` both rewrite history
(step 4d). Harmless for the author's own branches; call it out anyway.

Do not proceed until the user confirms. This is the **only** unconditional
stop; after it, the loop runs autonomously until it finishes or hits a
step-6 stop condition.

## Step 4 — The per-PR loop

For each PR in order, drive it to merged before touching the next one. At the
top of every iteration, **`git fetch origin` and re-read the PR's state** —
never act on stale JSON. `main` moves under a long run (other people merge;
earlier PRs in this batch land), and a PR you queued may itself have merged
out of band — if `state` is `MERGED`/`CLOSED`, drop it and move on.

Branch on `mergeStateStatus`:

- **`UNKNOWN`** — GitHub is still computing mergeability. Wait a few seconds
  and re-fetch. Do not act on `UNKNOWN`.
- **`DIRTY`** — merge conflict. Go to **step 5**. After resolution and a
  green re-run, come back through the loop.
- **`BEHIND`** — head is behind `main`. **Sync and sign** (step 4d), which
  re-triggers CI. A conflict during the rebase drops it to `DIRTY` → step 5.
- **`BLOCKED`** — mergeable but not allowed yet. Distinguish why:
  - commits **unsigned** (`required_signatures`) → **sync and sign** (4d).
  - required checks still **pending** → wait for checks (4a), then re-loop.
  - `reviewDecision` is `REVIEW_REQUIRED` or `CHANGES_REQUESTED` → this skill
    cannot approve. **Stop and ask** (step 6); a human must review.
- **`UNSTABLE`** — mergeable, but a check is failing or pending. Inspect the
  rollup: if the failure is a **required** check, treat it as red (4a). If
  only **non-required** checks are red/pending, surface which, and ask before
  merging past them — do not silently merge over a red check.
- **`HAS_HOOKS` / `CLEAN`** — ready, **but confirm every commit is signed
  first** (`gh api …/pulls/<pr>/commits --jq '[.[].commit.verification.verified]|all'`).
  If not signed → sync and sign (4d). In **safe** mode, if an earlier PR in
  this batch merged since this PR last ran CI — or `main` gained a fix this
  PR needs (see 4a) — sync and sign so it is tested against the `main` it
  will merge into. In **fast** mode, merge directly. Then **step 4c**.

### 4a. Wait for checks, and tell a flake from a real failure

Block on the head commit's checks rather than polling blindly (pipe-free —
piping `--watch` through `grep`/`head` closes its stdout and it exits early):

```bash
gh pr checks <pr> --watch --interval 60 --fail-fast > /tmp/pr<pr>.txt 2>&1
```

- **all required checks pass** → re-loop (the PR should now be `CLEAN`).
- **a required check fails** → do not blindly re-run, and do not blindly stop.
  **Read the failure log** (`gh run view <run-id> --log-failed`) and classify:
  - **Genuine failure** — reproduces on a PR that is **up to date with
    `main`**, and the same failure is not green on `main`. This is the PR
    author's to fix → **step 6**.
  - **Fixed upstream** — the PR is **behind** a `main` that already carries
    the fix (a stabilisation PR merged mid-run; compare the failing test
    against `main`'s current copy). Do not re-run the stale commit — **sync
    and sign** (4d) so CI re-runs against the fix.
  - **Known flake** — a non-deterministic failure documented as flaky (e.g.
    the e2e real-SW / IndexedDB isolation family) that passes on `main` and
    passed on this same content before. Re-run **just the failed job**
    (`gh run rerun <run-id> --failed`), **bounded to 2 retries**. If it is
    still red after that, treat it as genuine → step 6. Never loop re-runs to
    brute-force green — that masks a real intermittent bug.

Docs-only PRs skip the heavy jobs but still report `skipped` check runs,
which satisfy the ruleset — treat `skipped` required checks as passing, not
pending.

### 4b. (reserved — conflict handling lives in step 5)

### 4c. Merge

```bash
gh pr merge <pr> --squash --delete-branch   # omit --delete-branch if the head
                                            # branch is live in another worktree
                                            # (the local delete would fail) or the
                                            # user opted out
```

Confirm it landed (`gh pr view <pr> --json state` → `MERGED`), then bring the
local `main` forward so the next PR syncs against the true tip:

```bash
git checkout main && git pull --ff-only
```

If a local worktree exists for the merged branch, offer to clean it up from
the primary worktree with `bin/cleanup-merged-branch <branch>` — never run
that from inside a worktree it might delete.

Move to the next PR.

### 4d. Sync and sign

Rebase the PR onto **current `main`** and sign every commit in one operation,
in a throwaway detached worktree. This both brings the PR up to date (picking
up any fix that landed on `main`) and satisfies `required_signatures`. The
force-push re-triggers CI.

```bash
REPO=$(git rev-parse --show-toplevel)
git fetch origin --quiet
EXPECT=$(git rev-parse "origin/<headRef>")          # for --force-with-lease
WT="<scratchpad>/resign-<pr>"
git worktree add --detach "$WT" "origin/<headRef>" --quiet
cd "$WT"
git rebase -f --gpg-sign origin/main                # recreate + sign every commit
git log origin/main..HEAD --format='%G? %h %s'      # every line must start G (good sig)
git push --force-with-lease="<headRef>:$EXPECT" origin HEAD:"<headRef>"
cd "$REPO"                                           # BEFORE remove — see preconditions
git worktree remove --force "$WT"
```

Then wait for checks (4a). If the rebase hits a conflict, resolve it in the
same worktree (step 5) — you are already on the branch.

## Step 5 — Resolve a merge conflict

Reached for a `DIRTY` PR, or when the 4d rebase reports a conflict. Resolve in
the throwaway worktree (you are already there if 4d sent you here; otherwise
add one as in 4d), then verify and force-push. Because `required_signatures`
forces a history rewrite regardless, resolution is **rebase**-based (with
signing), not a merge:

```bash
# in the throwaway detached worktree on origin/<headRef>:
git rebase --gpg-sign origin/main       # stops on the first conflicting commit
# …resolve, git add -A, git rebase --continue (repeat until done)…
```

Classify each conflicted file:

- **Mechanical / regenerable — resolve without asking:**
  - `uv.lock` — re-resolve from `pyproject.toml`: take the merge, then
    `uv lock` to regenerate cleanly.
  - `perf/query_counts.txt` — regenerate after the code is merged:
    `uv run python manage.py monitor_query_counts --commit`.
  - Duplicate Django migration numbers (two `NNNN_*` on the same app) —
    renumber the incoming migration to follow `main`'s and fix its
    `dependencies`.
  - Append-only files (changelogs, registries) where both sides added
    distinct entries — keep both.
  - `static/css/output.css` is a gitignored build artefact and should not
    appear in a diff; if it does, someone committed it — flag that, don't
    hand-merge it.
- **Source code / templates / tests — resolve only if the intent is
  unambiguous** (e.g. two sides touched adjacent but independent lines).
  Present the resolved hunks to the user and **stop for confirmation before
  pushing** if the merge required any judgement about behaviour. A wrong
  conflict resolution silently ships a bug; err toward asking.

After the rebase completes, verify locally through the project's single entry
point before pushing — a conflict resolution that breaks a test must never
reach CI:

```bash
uv run tox                    # or the envs the conflict could affect, e.g. -e test -e mypy
```

If `tox` is red, the resolution is wrong — fix it, or stop and ask. Only
when green, force-push and clean up the worktree (as in 4d):

```bash
git log origin/main..HEAD --format='%G? %h %s'      # confirm every commit is signed
git push --force-with-lease origin HEAD:"<headRef>"
cd "$REPO" && git worktree remove --force "$WT"
```

Re-enter the loop for this PR; it should now progress toward `CLEAN`.

## Step 6 — Stop conditions

Stop the loop and report progress-so-far when any of these occur. Do **not**
skip the PR and carry on silently unless the user pre-authorised skipping.

- A **genuine required-check failure** (4a) — reproduces on a PR up to date
  with `main`, not a known flake and not already fixed on `main`. The author
  fixes it; this skill does not.
- A **known flake still red after 2 bounded re-runs** (4a) — the suite is too
  unstable to land against; hand it back rather than brute-forcing green.
- A conflict needs a **behavioural judgement** you are not certain about.
- A PR needs **human review** (`REVIEW_REQUIRED` / `CHANGES_REQUESTED`) and
  cannot be merged.
- **Signing cannot be satisfied** — `user.signingkey` unset, or the key isn't
  registered on GitHub (a re-signed commit still reports `verified=false`).
  This is the user's key to configure; do not guess one.
- `gh pr merge` is **rejected by the ruleset** for any other reason — surface
  the rejection verbatim; do not attempt an admin override or `--admin`
  bypass.

When stopping, say which PRs merged, which is blocked and why, and which are
still queued, so the user can act and re-run the skill to continue.

## Step 7 — Report

When the batch is exhausted (all merged, or the loop hit a stop):

```markdown
## Merge run complete

- **Merged:** #12 (SNOW-40), #15 (SNOW-41)
- **Blocked:** #18 (SNOW-42) — required check `Run tests` failed; author to fix
- **Not reached:** #21 (SNOW-43)
```

Note that each merged PR has auto-deployed to staging on Render and that
Linear tickets with a `Closes SNOW-xx` line have auto-transitioned to Done.
`main` is left checked out and up to date.
