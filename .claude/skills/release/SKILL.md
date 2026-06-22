---
name: release
description: |
  Cut a Snowdesk production release: open the release PR (main → release),
  get it merged so Render redeploys the three production services, then make
  sure the CalVer tag and GitHub Release exist — creating them as a fallback
  if the release.yml workflow did not. Use when the user says "/release",
  "cut a release", "ship to production", "do a release", or "release to
  prod". Do NOT use for a normal feature PR onto main (that is the
  `implement` skill), or for scoping/implementing a ticket.
allowed-tools: Bash, Read, Write, Skill, EnterPlanMode, ExitPlanMode
---

# Cut a Snowdesk release

Drive a production release end to end. The deploy is the side effect of
advancing the `release` branch; tagging and the GitHub Release are
retrospective housekeeping. Read [`docs/deployment.md`](../../docs/deployment.md)
for the full path-to-live model before changing anything here.

## What this skill owns vs. what CI owns

- Advancing `release` (via a release PR) is **yours** — that push triggers
  the Render production deploy.
- Tagging (CalVer `YYYY.MM.DD[.N]`) and the GitHub Release are **meant to be
  CI's** — [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
  fires on the push to `release`. This skill **verifies** that happened and
  **creates the tag + Release as a fallback** when it did not. Never create a
  second Release if CI already made one for the deployed commit.

> Note: a `push`-triggered workflow runs from the workflow file **in the
> pushed commit**. `release.yml` must therefore be present on the `release`
> tip after the merge for CI to fire. The first release after the workflow
> was introduced is the untested path — expect to use the fallback and
> confirm CI takes over on the next one.

## Steps

### 1. Preflight

Run these and stop with a clear message if any fails:

- On the primary worktree, working tree clean (`git status --porcelain`).
- `git fetch origin --tags --quiet`.
- `origin/main` is **ahead of** `origin/release`
  (`git rev-list --count origin/release..origin/main` > 0). If 0, there is
  nothing to release — stop.
- `main` CI is green: check the latest run on `main`
  (`gh run list --branch main --limit 1`). If the head commit's checks are
  failing or pending, surface it and ask before continuing.

### 2. Build the release preview as a plan

The preview doubles as the PR body, so compose it as GitHub-flavored
markdown and present it in the **plan pane** — it renders there exactly as
GitHub will render the PR, and approving the plan is the go-ahead to open
the PR with that body verbatim.

1. Collect the commits this release ships. Use the same comparison base
   `bin/cut-release` uses — `origin/release` when it exists (the normal
   case), else the most recent CalVer tag, else all of `origin/main`:

   ```bash
   git log origin/release..origin/main --format='%s'
   ```

2. Compose the PR body as markdown, rendering the tickets as a **table** —
   one row per `SNOW-xx` ticket, the **Change** column taken from the commit
   subject with the `SNOW-NN:` prefix stripped. Fold commits that share a
   ticket into one row (cite each PR number, e.g. `(#309, #315)`); put any
   ticketless commits in a final row with an em-dash (`—`) ticket. Shape:

   ```markdown
   Release to production: merges `main` onto `release`, triggering the
   production deploy and the Release workflow.

   ## Tickets in this release

   | Ticket | Change |
   |--------|--------|
   | SNOW-54 | Distinguish permanently-uncovered regions from no_rating tiles (#310) |
   | SNOW-298 | Day Risk Profile elevation glyph (#309, #315) |
   | — | chore: add /release skill (#316) |

   ---
   🤖 Opened with bin/cut-release
   ```

3. Write the composed markdown to `/tmp/release-pr-body.md` (Write), then
   present **its exact contents** as the plan via `ExitPlanMode`. The plan
   *is* the PR body — put nothing in the plan that should not appear on the
   PR.

4. Keep operational context out of the plan. State the computed CalVer tag
   (step 5 algorithm) and the "merging redeploys production on Render"
   warning in your chat message around the plan, not inside it.

### 3. Open (or update) the release PR

Once the plan is approved, open the PR with the **captured** body — never
regenerate it, so the PR matches the approved plan byte-for-byte. First
check whether a release PR is already open against `release`:

```bash
gh pr list --base release --state open
```

- None open → open it with the captured body:

  ```bash
  bin/cut-release --commit --body-file /tmp/release-pr-body.md
  ```

- One already open → reuse it; set its body to the approved markdown:

  ```bash
  gh pr edit <number> --body-file /tmp/release-pr-body.md
  ```

The `release` branch is branch-protected, so the merge goes through GitHub,
not a direct push. Print the PR URL.

### 4. Merge it

Merging advances `release` → Render redeploys the three production services
and (should) fire `release.yml`. Either:

- ask the user to merge in GitHub, then continue when they confirm; or
- if they tell you to merge it, use `gh pr merge <url> --merge` (use a
  merge commit, not squash — the release PR is a branch-advance, and the
  tickets already closed in Linear when they merged to `main`).

Then `git fetch origin --tags --quiet` and confirm `origin/release` now
equals the merged `main` tip.

### 5. Verify the tag + GitHub Release — fall back if missing

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
> `release.yml`. `bin/cut-release` uses `Release <date>` only as the *PR*
> title — do not copy that onto the Release.

### 6. Report

Tell the user:

- the CalVer tag and the Release URL;
- whether CI produced it or the fallback did (if the fallback ran, note that
  `release.yml` did not fire and why, so the automation can be fixed);
- a reminder that the production deploy is running on Render — point them at
  the dashboard to confirm the three services came up.

## Stop and ask if

- `main` CI is red or the latest `main` deploy was not verified on staging.
- The release PR has merge conflicts or failing required checks.
- A Release already exists for the deployed commit under an unexpected tag
  (do not create a duplicate — investigate first).
