"""
tests/bin/test_init_worktree.py — the SessionStart worktree bootstrap script.

``bin/init-worktree`` runs before the developer has typed anything, in a
directory Django may not yet be able to boot in. That gives it two
obligations that pull against each other:

* **leave the database usable** — the schema and the waffle manifest must
  match the checkout that is about to run against them, on EVERY session
  and not merely on the one that created the database (SNOW-997). The
  failure this replaced was silent: a worktree whose checkout had moved
  past its database got an early return and a "no such column" later;
* **stay out of the way** — a fully current worktree prints nothing, and
  the data seed never runs twice. ``loaddata`` / ``import_resorts`` /
  ``seed_test_data`` insert rows rather than reconcile them, so a second
  run would duplicate the seeded dataset rather than refresh it.

The tests drive the REAL script by subprocess against a synthetic git
worktree, with ``uv``, ``npm`` and ``npx`` stubbed onto ``PATH`` by
recording shims — following ``tests/bin/test_claude_hook_deny_command.py``.
What the script decided to run, in what order, IS the contract, so asserting
on the recorded argv is asserting on the thing itself. No Django process
starts and no database is created, which is what keeps these tests fast
enough to be worth having.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "init-worktree"

# The sentences the script greps for to decide "nothing changed". They
# are duplicated here deliberately rather than imported — there is nothing
# to import from a bash script, and a test that derived them from the
# script could not notice the script getting them wrong.
NO_MIGRATIONS = "No migrations to apply."
NO_FLAG_CHANGES = "Created 0 flag(s), deleted 0 flag(s)."
NO_ROUTE_CHANGES = "Canonical routes up to date."

# What a run that DID change something looks like, in each command's own
# output vocabulary.
MIGRATIONS_APPLIED = "Running migrations:\n  Applying routes.0007_thing... OK"
FLAGS_CHANGED = "Created 3 flag(s), deleted 0 flag(s)."
ROUTES_CHANGED = "Created mont-fort-backside.gpx (sampled)"

# The data half of the seed recipe: the three commands that must never run
# against a database that already exists.
DATA_SEED_COMMANDS = ("loaddata", "import_resorts", "seed_test_data")

_UV_STUB = """#!/usr/bin/env bash
# Records every invocation, then answers as the real manage.py command
# would. "$4" is the manage.py subcommand: run python manage.py <cmd> ...
echo "$*" >> "$STUB_LOG"
case "$4" in
    migrate)
        if [ "${STUB_MIGRATE_EXIT:-0}" -ne 0 ]; then
            echo "django.db.utils.OperationalError: no such table" >&2
            exit "$STUB_MIGRATE_EXIT"
        fi
        # The real migrate CREATES the database as a side effect, which is
        # why the script decides which path to take before calling it.
        touch db.sqlite3
        printf '%s\\n' "$STUB_MIGRATE_OUTPUT"
        ;;
    sync_waffle_flags)
        printf '%s\\n' "$STUB_WAFFLE_OUTPUT"
        ;;
    seed_canonical_routes)
        printf '%s\\n' "$STUB_ROUTES_OUTPUT"
        ;;
esac
# The real commands log to stderr on every run. Emitted here so the tests
# exercise the capture that keeps it off a quiet session's console.
echo "DEBUG apps.bulletins.services.prose prose parser registered" >&2
exit 0
"""

_RECORDING_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
exit 0
"""


@dataclass(frozen=True)
class Sandbox:
    """A synthetic main repo, one worktree of it, and the stub call log."""

    main: Path
    worktree: Path
    log: Path
    env: dict[str, str]

    def calls(self) -> list[str]:
        """Return every stubbed invocation, in the order it was made."""
        if not self.log.exists():
            return []
        return self.log.read_text().splitlines()

    def manage_commands(self) -> list[str]:
        """Return just the manage.py subcommands the script ran, in order."""
        return [
            line.split()[3]
            for line in self.calls()
            if line.startswith("run python manage.py ")
        ]


def _git(cwd: Path, *args: str) -> None:
    """Run one git command in `cwd`, failing the test on a non-zero exit."""
    subprocess.run(  # noqa: S603 - fixed executable, test-owned arguments
        ["git", *args],  # noqa: S607 - git is resolved from PATH by design
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    """Build a real git worktree with stubbed `uv`, `npm` and `npx`.

    Real git, because the script locates itself with
    ``git rev-parse --git-common-dir`` and the main-worktree refusal turns
    on that answer — a mocked git would be testing the mock.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The sandbox handle: both repo paths, the call log, and the
        environment the script must be run with.

    """
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "test@example.com")
    _git(main, "config", "user.name", "Test User")
    # The developer's global config signs every commit, and a gpg-agent
    # asked for signatures by several parallel workers at once fails some
    # of them — the intermittent exit 128 at the commit below. A throwaway
    # repository has nothing to sign.
    _git(main, "config", "commit.gpgsign", "false")
    (main / "README.md").write_text("placeholder\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "initial")
    # The two assets the script symlinks rather than builds.
    (main / ".env").write_text("SECRET_KEY=x\n")
    (main / ".venv").mkdir()

    worktree = tmp_path / "worktree"
    _git(main, "worktree", "add", str(worktree), "-b", "feature")
    # Pre-built by default: the CSS branch is a separate concern from the
    # database one, and a test that is not about it should not invoke npm.
    (worktree / "static" / "css").mkdir(parents=True)
    (worktree / "static" / "css" / "output.css").write_text("/* built */\n")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (
        ("uv", _UV_STUB),
        ("npm", _RECORDING_STUB),
        ("npx", _RECORDING_STUB),
    ):
        stub = stubs / name
        stub.write_text(body)
        stub.chmod(0o755)

    log = tmp_path / "calls.log"
    return Sandbox(
        main=main,
        worktree=worktree,
        log=log,
        env={
            # git is resolved from the real PATH; everything the script
            # would otherwise run for real is resolved from the stubs.
            "PATH": f"{stubs}:/usr/bin:/bin",
            "STUB_LOG": str(log),
            "STUB_MIGRATE_OUTPUT": NO_MIGRATIONS,
            "STUB_WAFFLE_OUTPUT": NO_FLAG_CHANGES,
            "STUB_ROUTES_OUTPUT": NO_ROUTE_CHANGES,
            "HOME": str(tmp_path),
        },
    )


def _run(sandbox: Sandbox, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the real script in `cwd` with the sandbox environment.

    Args:
        sandbox: The fixture handle.
        cwd: Directory to run from — the worktree, or the main repo for
            the refusal case.
        **env: Overrides merged over the sandbox environment, used to set
            the stubs' canned output per test.

    Returns:
        The completed process, un-checked: several tests assert on a
        non-zero exit.

    """
    return subprocess.run(  # noqa: S603 - absolute script, test-owned env
        ["bash", str(SCRIPT)],  # noqa: S607 - bash is resolved from PATH
        cwd=cwd,
        env={**sandbox.env, **env},
        capture_output=True,
        text=True,
    )


class TestAFreshWorktree:
    """The path that already worked: no database, so seed the whole thing."""

    def test_runs_the_whole_seed_recipe_in_order(self, sandbox: Sandbox) -> None:
        """All five commands, in the order the recipe documents.

        Order is load-bearing rather than incidental: ``migrate`` has to
        build the schema before ``loaddata`` can write to it, and
        ``import_resorts`` keys against the region fixtures that
        ``loaddata`` installs.
        """
        result = _run(sandbox, sandbox.worktree)

        assert result.returncode == 0, result.stderr
        assert sandbox.manage_commands() == [
            "migrate",
            "sync_waffle_flags",
            "loaddata",
            "import_resorts",
            "seed_test_data",
        ]

    def test_links_env_and_venv_from_the_main_repo(self, sandbox: Sandbox) -> None:
        """Slow-changing and identical everywhere, so one source of truth."""
        _run(sandbox, sandbox.worktree)

        assert (sandbox.worktree / ".env").is_symlink()
        assert (sandbox.worktree / ".env").resolve() == sandbox.main / ".env"
        assert (sandbox.worktree / ".venv").is_symlink()

    def test_builds_the_stylesheet_when_it_is_absent(self, sandbox: Sandbox) -> None:
        """output.css is a gitignored artefact, so each worktree builds its own."""
        (sandbox.worktree / "static" / "css" / "output.css").unlink()

        _run(sandbox, sandbox.worktree)

        assert any(line.startswith("install") for line in sandbox.calls())
        assert any("@tailwindcss/cli" in line for line in sandbox.calls())


class TestAnExistingDatabase:
    """SNOW-997 — the path that used to return early and do nothing."""

    @pytest.fixture(autouse=True)
    def _already_bootstrapped(self, sandbox: Sandbox) -> None:
        """Put the worktree in the state every session after the first finds.

        The symlinks matter as much as the database here. Without them the
        script links both on the way past and counts two actions it did
        not take on the database's behalf, which would make the quiet-run
        and action-count assertions below measure the wrong thing.
        """
        (sandbox.worktree / "db.sqlite3").write_text("")
        (sandbox.worktree / ".env").symlink_to(sandbox.main / ".env")
        (sandbox.worktree / ".venv").symlink_to(sandbox.main / ".venv")

    def test_the_schema_and_the_flag_manifest_are_brought_up_to_date(
        self, sandbox: Sandbox
    ) -> None:
        """The whole point: a second session still reconciles both.

        Before SNOW-997 this list was empty — the script saw a database
        and returned, so a checkout that had moved past it stayed
        unmigrated for the rest of that worktree's life.
        """
        result = _run(sandbox, sandbox.worktree)

        assert result.returncode == 0, result.stderr
        assert sandbox.manage_commands() == [
            "migrate",
            "sync_waffle_flags",
            "seed_canonical_routes",
        ]

    @pytest.mark.parametrize("command", DATA_SEED_COMMANDS)
    def test_the_data_seed_never_runs_a_second_time(
        self, sandbox: Sandbox, command: str
    ) -> None:
        """These three INSERT rows; re-running them duplicates the dataset.

        The regression guard on the fix itself. Running everything
        unconditionally would also have kept the schema current, and would
        have quietly doubled the seeded rows on every session.
        """
        _run(sandbox, sandbox.worktree)

        assert command not in sandbox.manage_commands()

    def test_it_says_nothing_when_everything_is_already_current(
        self, sandbox: Sandbox
    ) -> None:
        """A hook that chatters at every session start gets ignored.

        Both commands log to stderr on every real run, so silence here
        also pins that the script captures their output rather than
        letting it reach the console.
        """
        result = _run(sandbox, sandbox.worktree)

        assert result.stdout == ""
        assert result.stderr == ""

    def test_it_announces_migrations_it_applied(self, sandbox: Sandbox) -> None:
        """A database that WAS behind is worth a line, and the detail with it."""
        result = _run(sandbox, sandbox.worktree, STUB_MIGRATE_OUTPUT=MIGRATIONS_APPLIED)

        assert "brought db.sqlite3 up to date" in result.stdout
        assert "Applying routes.0007_thing" in result.stdout
        assert "applied 1 action(s)" in result.stdout

    def test_it_announces_a_changed_flag_manifest(self, sandbox: Sandbox) -> None:
        """A flag added to the manifest since seeding moves the query counts."""
        result = _run(sandbox, sandbox.worktree, STUB_WAFFLE_OUTPUT=FLAGS_CHANGED)

        assert "reconciled the waffle flag manifest" in result.stdout

    def test_it_announces_reconciled_canonical_routes(self, sandbox: Sandbox) -> None:
        """SNOW-1023: a worktree seeded before SNOW-989 gains the routes."""
        result = _run(sandbox, sandbox.worktree, STUB_ROUTES_OUTPUT=ROUTES_CHANGED)

        assert "reconciled the canonical routes" in result.stdout
        assert "mont-fort-backside.gpx" in result.stdout

    def test_unrecognised_output_is_reported_rather_than_swallowed(
        self, sandbox: Sandbox
    ) -> None:
        """The check degrades to noisy, never to silent.

        The no-op sentences belong to Django and to our own command, and
        either could be reworded. If that happens this script must start
        announcing every session — which someone notices and fixes —
        rather than concluding nothing ever changes, which is the exact
        silent-no-op failure SNOW-997 was raised for.
        """
        result = _run(
            sandbox,
            sandbox.worktree,
            STUB_MIGRATE_OUTPUT="Nothing doing, mate.",
        )

        assert "brought db.sqlite3 up to date" in result.stdout

    def test_a_failing_command_stops_the_script_and_claims_nothing(
        self, sandbox: Sandbox
    ) -> None:
        """A crash must not be read as "this changed something".

        The caller branches on a non-zero return to decide whether to
        announce, so a failure that returned rather than exiting would
        print "brought db.sqlite3 up to date" over a database that never
        migrated — and would then carry on to the next step.
        """
        result = _run(sandbox, sandbox.worktree, STUB_MIGRATE_EXIT="1")

        assert result.returncode != 0
        assert "brought db.sqlite3 up to date" not in result.stdout
        assert "no such table" in result.stderr
        assert "sync_waffle_flags" not in sandbox.manage_commands()


class TestTheMainWorktree:
    """The refusal, which is about the symlinks rather than the database."""

    def test_it_refuses_and_runs_nothing(self, sandbox: Sandbox) -> None:
        """Linking .env to itself is meaningless, so the script declines.

        Asserted on the call log as well as the exit code: refusing but
        having already migrated something would be a worse outcome than
        either refusing or proceeding cleanly.
        """
        result = _run(sandbox, sandbox.main)

        assert result.returncode != 0
        assert "refusing to run from the main worktree" in result.stderr
        assert sandbox.calls() == []


class TestSymlinkHandling:
    """``-L`` rather than ``-e``, and the case that distinction exists for."""

    def test_a_broken_symlink_is_left_alone(self, sandbox: Sandbox) -> None:
        """A stale link is not silently re-created on top of itself.

        ``-e`` follows the link and reports a broken one as absent, which
        would make the script try to create a symlink that already exists
        and fail the whole run on the ``ln`` error.
        """
        (sandbox.worktree / ".env").symlink_to(sandbox.main / "gone")

        result = _run(sandbox, sandbox.worktree)

        assert result.returncode == 0, result.stderr
        assert (sandbox.worktree / ".env").is_symlink()
        assert not (sandbox.worktree / ".env").exists()
