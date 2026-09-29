---
name: release
description: |
  Cut a Snowdesk production release: run `bin/cut-release --commit` to open
  the release PR (one commit bumping `VERSION`, the ticket list and any
  post-deploy commands in its description), and after the human merges it,
  confirm release-sync fast-forwarded `release` and the CalVer tag and GitHub
  Release exist. Use when the user says "/release", "cut a release", "ship to
  production", "do a release", or "release to prod". Do NOT use for a normal
  feature PR onto main (that is the `implement` skill), or for
  scoping/implementing a ticket.
allowed-tools: Bash, Read
# Merging the release PR deploys production. Only a human may start this.
disable-model-invocation: true
---

# Cut a Snowdesk release

A release is one pull request. `bin/cut-release --commit` opens it against
`main`: a single signed commit bumping `VERSION` to the next ordinal, on a
`release-vNN` branch, with every `SNOW-xx` ticket production has not yet seen
in the description. **Merging that PR is the release.**
[`release-sync.yml`](../../../.github/workflows/release-sync.yml) sees
`VERSION` change on `main`, waits for that commit's required checks,
fast-forwards `release` to it (Render deploys production), and dispatches
[`release.yml`](../../../.github/workflows/release.yml) to tag the commit
CalVer and create the GitHub Release. Read
[`docs/deployment.md`](../../../docs/deployment.md) before changing anything
here.

## What this skill owns vs. what CI owns

- **Yours:** preflight, the release preview, the post-deploy command audit,
  opening the PR with `bin/cut-release --commit`, and — once the human has
  merged — confirming the sync, tag and Release happened.
- **Never yours:** merging the release PR, or pushing `release`. The merge is
  the production deploy and the human's call. `release-sync.yml` advances
  `release`; nothing in this skill pushes it.
- **CI's:** the fast-forward, the CalVer tag, the GitHub Release.

## Steps

### 1. Preflight

Stop with a clear message if any of these fails. `bin/cut-release` repeats
the ref checks and refuses on its own, but surface them before the preview.

- `git fetch origin --tags --quiet`.
- `origin/main` is ahead of `origin/release`
  (`git rev-list --count origin/release..origin/main` > 0). If 0, there is
  nothing to release.
- `origin/release` is an ancestor of `origin/main`
  (`git merge-base --is-ancestor origin/release origin/main`). If not,
  `release` was moved out of band — investigate, never force.
- `VERSION` matches on both refs (`git show origin/main:VERSION` vs
  `git show origin/release:VERSION`). A mismatch means a release PR merged
  and the sync has not finished — check
  `gh run list --workflow release-sync.yml`.
- No `release-vNN` branch or open release PR already exists
  (`gh pr list --search "Release v" --state open`).
- `main`'s head commit passes the checks that gate a release. The "Release
  branch" ruleset (id `19141574`) is the source of truth for which those
  are — read its required contexts, then their conclusions on the commit:

  ```bash
  gh api repos/{owner}/{repo}/rulesets/19141574 \
      --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'
  gh api repos/{owner}/{repo}/commits/<sha>/check-runs --paginate \
      --jq '.check_runs[] | "\(.name)\t\(.status)\t\(.conclusion)"'
  ```

  Every required context must be `success`. A failing check outside that
  list (e.g. `Dependency audit (dev + npm)`, detection-only) does not block
  the release — mention it, do not stop on it. `gh run list --branch main`
  can miss the head SHA; read the check runs on the commit itself.

### 2. Dry run and release preview

```bash
bin/cut-release
```

The dry run prints the next version (`vNN → vNN+1`), the target SHA, the
ticket list, the PR body, and a stderr warning for any newly added one-shot
data command with no `Deploy-Step:` trailer. It pushes nothing.

Present the preview in chat as a table, one row per `SNOW-xx` ticket, the
**Change** column from the commit subject with the `SNOW-NN:` prefix
stripped. Fold commits sharing a ticket into one row (cite each PR number);
put ticketless commits in a final `—` row:

```markdown
| Ticket | Change |
|--------|--------|
| SNOW-54 | Distinguish permanently-uncovered regions from no_rating tiles (#310) |
| — | Bump djangofmt; patch npm audit (#999, #1004) |
```

State the version, the target SHA, and that **merging** the PR (not opening
it) redeploys production.

### 3. Audit post-deploy commands

The PR's "Run after this deploys" section comes only from `Deploy-Step:`
commit trailers. The script's backstop only warns on *newly added*
`backfill_|fill_|link_|import_` commands, so a ticket that widens an
existing backfill's candidate set (SNOW-1043 did, for
`backfill_route_slope_samples` and `backfill_trip_slope_samples`) slips
through both. Check the range yourself:

```bash
git log origin/release..origin/main --format='%B' | grep '^Deploy-Step:'
git diff --name-status origin/release origin/main -- 'apps/*/management/commands/*.py' 'apps/*/migrations/*.py' schedule.py config/
```

- An added or **modified** one-shot data command: read its diff and the
  ticket's commit body to decide whether production needs a run.
- Migrations run on deploy (`build.sh`); flag only a data migration that
  says it needs a follow-up command.
- A new scheduled command wired into `schedule.py` needs no manual run.
- A new setting read from the environment needs the Render env var set
  **before** the merge.

List what production needs in the preview. If the script's body will not
carry it, add a `## Run after this deploys` section to the PR description
after step 4 (`gh pr edit <n> --body-file …`), before the
`🤖 Generated with` line, commands in the form the Render shell takes
(`python manage.py …`, no `uv run`).

Ask the user to confirm before opening the PR.

### 4. Open the release PR

```bash
bin/cut-release --commit
```

It builds the bump commit with git plumbing (the working tree and checked-out
branch are untouched, so any worktree works), signs it with `-S`, pushes
`release-vNN`, and opens the PR with `gh pr create`.

Two things can stop it:

- **Auto mode's permission check** classifies it as a production deploy.
  Stop and tell the user; they approve the prompt or run the command in
  their own terminal.
- **The sandbox.** The script needs `mktemp -d` (the macOS temp directory),
  GPG signing (`~/.gnupg` and the agent socket), and git over HTTPS. On a
  sandbox refusal, stop, name what was refused and the setting that would
  allow it, and wait — do not re-run outside the sandbox. `mktemp` fails
  before the push, so a refusal there leaves nothing pushed.

Then bind the PR to the session (`ccd_pr` `get_status`, `bind_pr` if
unbound) and turn Auto-fix on with `set_monitor`. Apply step 3's
description edit if one is needed.

Stop here. Report the PR URL, the version, any post-deploy commands, and
that merging deploys production.

### 5. After the merge — verify, fall back only if needed

Only when the user says the PR is merged (or asks you to check).

Verify against the release PR's **merge commit**, not `main`'s tip:
`release-sync.yml` advances `release` to the fixed SHA of the push that
changed `VERSION`, and another PR may have landed on `main` since.

```bash
sha=$(gh pr view <n> --json mergeCommit --jq .mergeCommit.oid)
gh run list --workflow release-sync.yml --commit "$sha"
gh run list --workflow release.yml --limit 3
git fetch origin --tags --quiet
test "$(git rev-parse origin/release)" = "$sha" && echo "release == release PR merge ✓"
git tag --points-at "$sha"
gh release list --limit 5
```

The expected tag is today's CalVer, matching `release.yml`:
`date=$(date -u +'%Y.%m.%d')`; with no `$date` / `$date.*` tag yet it is
`$date`, otherwise the next free `.N` (the bare date counts as `.1`).

- **A tag and Release exist** on `$sha` → report the tag and URL. Create
  nothing.
- **release-sync failed or is still waiting** (`release` is not yet `$sha`)
  → report the run and its failing step. Do not push `release` yourself.
- **`release` is `$sha` but no tag/Release** (release.yml did not run or
  failed) → create them against `$sha`, the same way CI does:

  ```bash
  gh release create "<tag>" \
      --target "$sha" \
      --title "<tag>" \
      --generate-notes
  ```

### 6. Report

- the CalVer tag and Release URL, and whether CI or the fallback made it;
- the post-deploy commands to run on the production web service's Render
  shell once it is up;
- that the deploy runs on Render — point at the dashboard to confirm the
  three services (web, scheduler, task worker) came up.

## Stop and ask if

- A required check on `main`'s head is failing or pending, or its staging
  deploy was not verified.
- `release` is not an ancestor of `main`, or `VERSION` differs between them.
- A release PR or `release-vNN` branch already exists.
- The sandbox or the permission check refuses a step.
- A Release already exists for the deployed commit under an unexpected tag
  — do not create a duplicate.
