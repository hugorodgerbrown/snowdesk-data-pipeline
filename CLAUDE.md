# CLAUDE.md — Snowdesk

@AGENTS.md

The rest of this file applies to Claude Code only. A rule that binds any agent
goes in `AGENTS.md` ([why](docs/decisions/agents-md-is-the-rulebook.md)).

## Who does what

Claude Code scopes and implements every ticket; Codex reviews scopes and PRs.

- Skills in `.claude/skills/` run the lifecycle: `scope` / `create-ticket`,
  `resolve-scope-review`, `implement`, `work-on`, `merge-prs`, `release`.
- Say one line when starting a skill, such as "Running `implement` for SNOW-123."
  No rule may depend on which skill is running unless Hugo can see it.
- `implement` runs from `Ready for dev` to a merged PR with no stop, unless Hugo
  adds one such as "stop before pushing". Hugo does not review the plan.
- One session works one ticket. Warn before starting work on a second, unless the
  session was framed as cross-ticket from the start.

## Commits

- Commit without asking, in any session, once the work is verified. Stage named
  files. Hugo reviews at the PR and can ask for a commit to be held.
- Once a commit lands, push and open the PR without asking.
- Force-push and resetting pushed history need Hugo's approval.
- Merge the PR you opened under `AGENTS.md` → Merging, whichever skill is running.

## Worktrees and hooks

- `bin/init-worktree` seeds `db.sqlite3` in a worktree at session start. Credentials
  and the force-reseed procedure: [`docs/worktrees.md`](docs/worktrees.md).
- A PreToolUse hook refuses a bare `pytest`. All hooks:
  [`docs/claude-code-hooks.md`](docs/claude-code-hooks.md).
- A cloud session holds no provider API keys
  ([why](docs/decisions/cloud-sessions-hold-no-provider-credentials.md)).
