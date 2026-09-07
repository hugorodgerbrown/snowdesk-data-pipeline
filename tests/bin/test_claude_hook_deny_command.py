"""
tests/bin/test_claude_hook_deny_command.py — the PreToolUse command deny hook.

``bin/claude-hook-deny-command`` sits in front of every Bash tool call in a
Claude Code session, so it has two jobs and both are absolute:

* **deny what is prohibited** — a shared-stash ``git stash pop``, a bare
  ``pytest``, a ``git commit`` that would record the wrong author. Each of
  those is prohibited in prose today and enforced by nothing, and a check
  that prints a warning and lets the command through is indistinguishable
  from no check at all;
* **never deny anything else, and never fail** — malformed JSON, empty
  stdin or a missing key must exit 0 in silence. An exception here does not
  fail one call, it blocks every Bash call for the rest of the session.

The tests drive the real script by subprocess, feeding it the event JSON
shape Claude Code puts on stdin, because the exit code and the stdout
payload together *are* the contract.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "claude-hook-deny-command"

# The script declares `#!/usr/bin/env python3` and imports nothing outside the
# stdlib, so the interpreter running the suite serves as well as any other.
PYTHON = shutil.which("python3") or sys.executable


def _run(stdin: str) -> subprocess.CompletedProcess[str]:
    """Run the hook with `stdin` on its standard input."""
    return subprocess.run(  # noqa: S603 - absolute executable, test-owned input
        [PYTHON, str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _event(command: str) -> str:
    """Build the PreToolUse event JSON Claude Code sends for a Bash call."""
    return json.dumps(
        {
            "session_id": "test-session",
            "cwd": "/repo",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "description": "test"},
        }
    )


def _decision(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """Parse the hook's stdout into its `hookSpecificOutput` block."""
    payload: dict[str, dict[str, str]] = json.loads(result.stdout)
    return payload["hookSpecificOutput"]


def assert_denied(command: str) -> str:
    """Assert `command` is denied, and return the reason shown to the model."""
    result = _run(_event(command))
    assert result.returncode == 0, result.stderr
    decision = _decision(result)
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"]
    return decision["permissionDecisionReason"]


def assert_allowed(command: str) -> None:
    """Assert `command` passes through: exit 0, and nothing on stdout."""
    result = _run(_event(command))
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


# ── git stash ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "git stash",
        "git stash -u",
        "git stash pop",
        "git stash pop --index",
        "git -C /repo stash pop",
    ],
)
def test_denies_the_unlabelled_and_destructive_stash_forms(command: str) -> None:
    """A bare stash and a pop both act on a stack every worktree shares."""
    assert "stash" in assert_denied(command)


@pytest.mark.parametrize(
    "command",
    [
        'git stash push -u -m "SNOW-858: half-finished hook"',
        "git stash list",
        "git stash show",
        "git stash apply 0f1e2d3",
        "git stash drop 0f1e2d3",
    ],
)
def test_allows_the_stash_forms_that_name_what_they_touch(command: str) -> None:
    """`push -m`, `list`, `apply <sha>` and `drop` all say which entry."""
    assert_allowed(command)


# ── pytest ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "pytest tests/",
        "pytest tests/bin -x",
        "DJANGO_SETTINGS_MODULE=config.settings.development pytest tests/",
    ],
)
def test_denies_a_bare_pytest(command: str) -> None:
    """tox and `uv run` are the sanctioned entry points; PATH is not."""
    assert "uv run pytest" in assert_denied(command)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest tests/",
        "uv run pytest tests/bin -x",
        "uv run tox -e test",
    ],
)
def test_allows_pytest_through_uv(command: str) -> None:
    """`uv run pytest` is what the implement skill prescribes for a targeted run."""
    assert_allowed(command)


# ── git commit ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "SNOW-858: x"',
        "git commit -a",
        'git -C /repo commit -m "SNOW-858: x"',
    ],
)
def test_denies_a_commit_with_no_author(command: str) -> None:
    """The author/committer split is what keeps a commit Verified."""
    assert "--author" in assert_denied(command)


@pytest.mark.parametrize(
    "command",
    [
        'git commit --author="Claude <noreply@anthropic.com>" -m "SNOW-858: x"',
        'git commit --author "Claude <noreply@anthropic.com>" -m "SNOW-858: x"',
        "git commit --amend --no-edit",
    ],
)
def test_allows_a_commit_that_records_the_right_author(command: str) -> None:
    """`--author` sets it; `--amend` preserves the one already recorded."""
    assert_allowed(command)


# ── everything else ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline -5",
        "ls bin/",
        "uv run tox -e ds-lint",
        # `pytest` as an argument, not as the command being run.
        "grep -rn pytest tox.ini",
        "echo 'run git stash pop to restore'",
    ],
)
def test_allows_unrelated_commands(command: str) -> None:
    """Only the command word of each segment is a verdict; arguments are not."""
    assert_allowed(command)


def test_denies_a_prohibited_command_chained_behind_an_allowed_one() -> None:
    """Each `&&` segment is judged on its own command word."""
    assert_denied("git add -A && git commit -m 'SNOW-858: x'")


# ── malformed input: the session-blocking cases ────────────────────────────


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "   ",
        "not json at all",
        '{"tool_input": ',
        "[]",
        '"a bare string"',
        "null",
        '{"tool_name": "Bash"}',
        '{"tool_name": "Bash", "tool_input": null}',
        '{"tool_name": "Bash", "tool_input": "not an object"}',
        '{"tool_name": "Bash", "tool_input": {}}',
        '{"tool_name": "Bash", "tool_input": {"command": 42}}',
    ],
)
def test_exits_silently_on_input_it_cannot_read(stdin: str) -> None:
    """An exception here would block every Bash call for the rest of a session."""
    result = _run(stdin)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_exits_silently_on_a_command_it_cannot_tokenise() -> None:
    """An unbalanced quote yields no segments rather than an unhandled error."""
    result = _run(_event("echo 'unterminated"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
