---
name: release
description: |
  Cut a Snowdesk production release: fast-forward `release` to `main` so
  Render redeploys the three production services, then make sure the CalVer
  tag and GitHub Release exist — creating them as a fallback if the
  release.yml workflow did not. Use when the user says "/release", "cut a
  release", "ship to production", "do a release", or "release to prod". Do
  NOT use for a normal feature PR onto main (that is the `implement` skill),
  or for scoping/implementing a ticket.
allowed-tools: Bash, Read
# Advancing `release` deploys production. Only a human may start this.
disable-model-invocation: true
---

# Cut a Snowdesk release

Drive a production release end to end. `release` behaves like a tag that
moves with `main`: advancing it is a **fast-forward** to the current `main`
tip, so the production commit is byte-identical to the `main` commit already
verified on staging — no merge commit, no divergence, no release PR. The
deploy is the side effect of that fast-forward; tagging and the GitHub
Release are retrospective housekeeping. Read
[`docs/deployment.md`](../../docs/deployment.md) for the full path-to-live
model before changing anything here.

## What this skill owns vs. what CI owns

- Advancing `release` (a fast-forward push to `origin/main`'s SHA) is
  **yours** — that push triggers the Render production deploy.
- Tagging (CalVer `YYYY.MM.DD[.N]`) and the GitHub Release are **meant to be
  CI's** — [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
  fires on the push to `release`. This skill **verifies** that happened and
  **creates the tag + Release as a fallback** when it did not. Never create a
  second Release if CI already made one for the deployed commit.

> Note: a `push`-triggered workflow runs from the workflow file **in the
> pushed commit**. `release.yml` must therefore be present on the `release`
> tip after the fast-forward for CI to fire. The first release after the
> workflow was introduced is the untested path — expect to use the fallback
> and confirm CI takes over on the next one.

## Steps

### 1. Preflight

Run these and stop with a clear message if any fails:

- On the primary worktree, working tree clean (`git status --porcelain`).
- `git fetch origin --tags --quiet`.
- `origin/main` is **ahead of** `origin/release`
  (`git rev-list --count origin/release..origin/main` > 0). If 0, there is
  nothing to release — stop.
- The advance is a genuine fast-forward: `origin/release` is an ancestor of
  `origin/main` (`git merge-base --is-ancestor origin/release origin/main`).
  If not, `release` was advanced out of band — stop and investigate rather
  than forcing anything.
- `main` CI is green: check the latest run on `main`
  (`gh run list --branch main --limit 1`). If the head commit's checks are
  failing or pending, surface it and ask before continuing. The "Release
  branch" ruleset will reject the fast-forward push unless those checks are
  green on the target commit, so a red `main` cannot ship.

### 2. Build the release preview

Show the user what this release ships so they can confirm before production
moves. This is a preview for the human, **not** a PR body (there is no PR)
and **not** the GitHub Release notes (release.yml generates those from the
merged PRs). Present it in your chat message, not the plan pane.

1. Collect the commits this release ships. Use the same comparison base
   `bin/cut-release` uses — `origin/release` when it exists (the normal
   case), else the most recent CalVer tag, else all of `origin/main`:

   ```bash
   git log origin/release..origin/main --format='%s'
   ```

2. Render the tickets as a **table** — one row per `SNOW-xx` ticket, the
   **Change** column taken from the commit subject with the `SNOW-NN:`
   prefix stripped. Fold commits that share a ticket into one row (cite each
   PR number, e.g. `(#309, #315)`); put any ticketless commits in a final
   row with an em-dash (`—`) ticket. Shape:

   ```markdown
   ## Shipping to production

   | Ticket | Change |
   |--------|--------|
   | SNOW-54 | Distinguish permanently-uncovered regions from no_rating tiles (#310) |
   | SNOW-298 | Day Risk Profile elevation glyph (#309, #315) |
   | — | chore: add /release skill (#316) |
   ```

3. State the target SHA (`origin/main`'s short SHA), the computed CalVer tag
   (step 4 algorithm), and that fast-forwarding `release` **redeploys
   production on Render**. Ask the user to confirm before advancing.

### 3. Advance `release` (fast-forward)

Once the user confirms, fast-forward `release` to `main`:

```bash
bin/cut-release --commit
```

This pushes `origin/main`'s exact SHA to `refs/heads/release`. The ruleset
allows it only as a fast-forward whose target commit's required checks are
already green. There is no PR and no merge commit — `origin/release` now
equals `origin/main`. Confirm that:

```bash
git fetch origin --tags --quiet
test "$(git rev-parse origin/release)" = "$(git rev-parse origin/main)" \
    && echo "release == main ✓"
```

The push triggers the Render production deploy and (should) fire
`release.yml`.

### 4. Verify the tag + GitHub Release — fall back if missing

Compute today's expected CalVer tag, matching `release.yml` exactly:

- `date=$(date -u +'%Y.%m.%d')`.
- Existing tags for today: `git tag --list "$date" "$date.*"`.
- None → tag is `$date`. Otherwise the next free `.N` suffix (the bare date
  counts as `.1`, so a second release the same day is `$date.2`).

Watch for the workflow, then check:

```bash
gh run list --workflow=release.yml --limit 3      # did it fire?
gh release list --limit 5                          # did a Release appear?
```

- **CI created the Release** for the deployed commit → report the tag, the
  Release URL, and stop. Do not create anything.
- **CI did not** (no run, failed run, or no Release) → create them yourself,
  targeting the deployed `release` tip, using auto-generated notes (same as
  CI):

  ```bash
  gh release create "<tag>" \
      --target "$(git rev-parse origin/release)" \
      --title "<tag>" \
      --generate-notes
  ```

  This both creates the tag and the Release in one call.

> The Release **title** is the bare tag (`2026.06.22`), matching
> `release.yml`.

### 5. Report

Tell the user:

- the CalVer tag and the Release URL;
- whether CI produced it or the fallback did (if the fallback ran, note that
  `release.yml` did not fire and why, so the automation can be fixed);
- a reminder that the production deploy is running on Render — point them at
  the dashboard to confirm the three services came up.

## Stop and ask if

- `main` CI is red or the latest `main` deploy was not verified on staging.
- The advance would not be a fast-forward (`release` is not an ancestor of
  `main`) — someone moved `release` out of band; investigate first.
- The fast-forward push is rejected (e.g. the target commit's required
  checks are not green) — surface the rejection rather than retrying.
- A Release already exists for the deployed commit under an unexpected tag
  (do not create a duplicate — investigate first).
