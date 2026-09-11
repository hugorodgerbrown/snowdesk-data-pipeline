---
name: claude-code-hooks
description: Claude Code hooks wired in .claude/settings.json — setup-remote-env, init-worktree, mark-worktree-cleanable, claude-hook-deny-command
status: current
last-reviewed: 2026-09-07
---

# Claude Code hooks

Four scripts run automatically inside a Claude Code session, wired in the
`hooks` block of [`.claude/settings.json`](../.claude/settings.json). They are
the only automation in this repo that fires without anyone asking for it, so
each one is listed here with what it does, when it runs, and what it refuses to
do.

All four live in `bin/`. That is where every hook script has always lived, and
the committed `Bash(bin/:*)` permission grant covers the path.

| Event | Matcher | Script | What it does |
|---|---|---|---|
| `SessionStart` | — | [`bin/setup-remote-env`](../bin/setup-remote-env) | Provisions Python 3.14, `.venv`, `.env`, the dev database and `output.css` in a **cloud** session |
| `SessionStart` | — | [`bin/init-worktree`](../bin/init-worktree) | Seeds a fresh git worktree: symlinks `.env`/`.venv`, builds `db.sqlite3`, compiles the stylesheet |
| `SessionEnd` | — | [`bin/mark-worktree-cleanable`](../bin/mark-worktree-cleanable) | Tombstones the worktree if — and only if — it is clean and fully merged |
| `PreToolUse` | `Bash` | [`bin/claude-hook-deny-command`](../bin/claude-hook-deny-command) | Refuses three prohibited commands before they run |

A hook is read from `settings.json` **at session start**. Editing the block
does not affect the session that edited it, which is why the wiring cannot be
proved from inside the session that added it.

## SessionStart

Both entries are wrapped `2>&1 || true` in `settings.json`, so neither can
block a session from starting. That is also why both scripts print a loud
banner on failure rather than merely exiting non-zero: the exit code is
discarded, and the text is the only signal a developer ever sees.

### `bin/setup-remote-env`

A no-op unless `CLAUDE_CODE_REMOTE` is `true`, so a local machine is never
touched. In a cloud container it resolves a **final** CPython 3.14 (a release
candidate is rejected — 3.14.0rc2 satisfied a naive `>= 3.14` test and left the
session unable to `import django`), rebuilds `.venv` when it is missing or
cannot import Django, seeds a throwaway `.env` with a random `SECRET_KEY`,
verifies the result with `manage.py check`, then seeds `db.sqlite3` and builds
`static/css/output.css`.

The seed is `bin/init-worktree`'s recipe **copied**, step for step — migrate
→ `sync_waffle_flags` → region fixtures → `import_resorts` →
`seed_test_data` — because that script refuses to run in the main worktree
and a cloud session clones exactly that. Copied means the two can drift, and
they had: this one ran `loaddata eaws_CH resorts`, and `resorts` is not a
fixture (the curated sheet arrives through `import_resorts --commit`). The
chain stopped there, the half-built database was removed, and every remote
session started with no data — announced only as one WARNING in a hook log
that nobody reads on a session that otherwise looks fine. **Change one
recipe, change the other.**

Idempotent: every step checks whether it is already done, so a warm container
costs about a second. The database check asks whether region rows exist
rather than whether the file does, because the `manage.py check` above has
already created an empty SQLite file by connecting to it.

### `bin/init-worktree`

Refuses to run in the main worktree. In a worktree it symlinks `.env` and
`.venv` back to the main repo and, when `db.sqlite3` is absent, runs the seed
recipe (migrate → `sync_waffle_flags` → region fixtures → resorts →
`seed_test_data`, which includes the dev users).
Full recipe, dev credentials and the force-reseed procedure:
[`docs/worktrees.md`](worktrees.md).

## SessionEnd

### `bin/mark-worktree-cleanable`

Writes a `READY_FOR_CLEANUP` marker into the worktree's git metadata directory,
which [`bin/sync-with-origin`](../bin/sync-with-origin) picks up on its next
run. It cannot remove the worktree itself — `git worktree remove` cannot run
from inside the worktree it would delete — so the tombstone hands that off.

The gate matters more than the marker. A session ends every time the developer
steps away mid-feature, so the script sets the marker only when the working
tree is clean **and** `HEAD` is already reachable from `origin/main`. It is
silent on the no-op path, which is the common one.

## PreToolUse

### `bin/claude-hook-deny-command`

Matcher `Bash`. Reads the tool-call event JSON on stdin and, for three
commands, writes a `permissionDecision: "deny"` payload on stdout. A deny stops
the call and hands the reason back to the model, so each reason names what to
run instead.

| Refused | Allowed | Why |
|---|---|---|
| `git stash`, `git stash pop` | `git stash push -u -m "…"`, `list`, `show`, `apply <sha>`, `drop` | The stash stack belongs to the repository, not the worktree. Every worktree shares one list, so a bare stash leaves an unlabelled entry and `pop` can take another session's work off the top and drop it |
| `pytest …` | `uv run pytest …`, `uv run tox -e test` | A bare `pytest` resolves against whatever interpreter is on PATH — usually the system 3.9, which cannot parse this project's `except A, B:` syntax |
| `git commit` with no `--author` | `git commit --author=…`, `git commit --amend` | Claude authors and Hugo commits; that split is what keeps a commit Verified under the main branch's signature ruleset, and the flag is silently absent by default |

Only the **command word** of each `&&`/`;`/`|` segment is judged, so `grep -rn
pytest tox.ini` is not a pytest invocation and an argument list is never
mistaken for one.

**Every failure path exits 0 in silence** — malformed JSON, empty stdin, a
missing key, an untokenisable command. This script sits in front of every Bash
call in a session; an unhandled exception here would not fail one call, it
would block every call for the rest of the session. Coverage for exactly that
is in
[`tests/bin/test_claude_hook_deny_command.py`](../tests/bin/test_claude_hook_deny_command.py).

## What is deliberately not a Claude Code hook

**The lint guards.** `ds-lint`, `i18n-lint`, `js-globals-lint` and `docs-lint`
run as **pre-commit** hooks (see
[`.pre-commit-config.yaml`](../.pre-commit-config.yaml)), not from here. They
are file checks, and pre-commit is the mechanism this repo already uses for
file checks: it protects every committer — Hugo's own commits, another agent's,
any editor — not only a Claude Code session, and it needs no dispatcher of its
own. Their tox envs and the `lint-guards` CI workflow are unchanged, and that
backstop is what makes a bypassable local hook safe.

`djangofmt` is on the same footing, which is why "run djangofmt after editing a
template" needs no hook at all.

**A `PostToolUse` format-and-lint wrapper.** Once the guards are pre-commit
hooks, a second mechanism doing the same job for one caller is exactly the
abstraction the "no abstractions until two callers need them" rule rules out.

## Adding a hook

Read this first: three of the four scripts above exist because a *repeated*
failure justified automation, and the rule in
[`.claude/README.md`](../.claude/README.md) is that friction repeated three
times is what earns a hook. One annoyance does not.

If it survives that test:

1. Put the script in `bin/`, extensionless, with a header block explaining why
   it exists and what it refuses to do.
2. Python only if it must parse the event JSON — `jq` is not a repo dependency.
   Stdlib only, no Django: a hook runs before anything in the session is set up.
3. Never raise. A hook that throws degrades every session it runs in, and the
   symptom is nothing like the cause.
4. Add it to the table at the top of this doc.
5. If it is a Python script in `bin/`, add it to `[tool.ruff] extend-include` in
   `pyproject.toml` — `tests/bin/test_migrations_lint.py` fails otherwise.
