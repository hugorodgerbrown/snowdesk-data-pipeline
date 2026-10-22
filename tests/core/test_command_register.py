"""
tests/core/test_command_register.py — the command run register (SNOW-1189).

``RegisteredCommand`` records every run of a project command as a
``CommandRun`` row. The tests pin the four rules in its module docstring:

* a dry run is recorded, even when the command rolls its own work back;
* the exit code and the exception are untouched — the row records them and
  the command still raises;
* a failure to write the row never stops or changes the command;
* the row carries the build, the operator and what started the run.

The commands under test are small classes defined here, run through
``call_command`` (which accepts an instance) or ``run_from_argv``, so each
test controls exactly what ``handle()`` does.
"""

from __future__ import annotations

from argparse import ArgumentParser
from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import transaction
from django.test import override_settings

from apps.core.command_register import (
    OUTPUT_TAIL_CHARS,
    REDACTED,
    RegisteredCommand,
    _Tail,
    recorded_arguments,
    redacted_argv,
)
from apps.core.models import CommandRun
from apps.locations.models import Location
from tests.factories import CommandRunFactory, LocationFactory

pytestmark = pytest.mark.django_db


class _Probe(RegisteredCommand):
    """A ``--commit`` command whose ``handle`` is set per test."""

    behaviour: Any = None

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register --commit and a credential-looking option."""
        parser.add_argument("--commit", action="store_true")
        parser.add_argument("--api-key", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        """Print a line, then do whatever the test asked for."""
        self.stdout.write("probe ran")
        if self.behaviour is not None:
            self.behaviour(self, options)


class _ReadOnly(RegisteredCommand):
    """A command with no --commit flag."""

    def handle(self, *args: Any, **options: Any) -> None:
        """Do nothing."""


class _Unrecorded(_ReadOnly):
    """A command that opts out of the register."""

    record_runs = False


def _run(behaviour: Any = None, *args: str, **options: Any) -> _Probe:
    """Run a fresh ``_Probe`` through call_command and return it."""
    command = _Probe()
    command.behaviour = behaviour
    call_command(command, *args, stdout=StringIO(), stderr=StringIO(), **options)
    return command


def _only_run() -> CommandRun:
    """Return the one recorded run."""
    return CommandRun.objects.get()


# -- mode and outcome --------------------------------------------------------


def test_a_dry_run_is_recorded() -> None:
    """Without --commit the run is a DRY_RUN that succeeded."""
    _run()
    run = _only_run()
    assert run.mode == CommandRun.MODE.DRY_RUN
    assert run.status == CommandRun.STATUS.SUCCEEDED
    assert run.exit_code == 0
    assert run.finished_at is not None
    assert run.duration is not None


def test_a_commit_run_is_recorded_as_commit() -> None:
    """--commit makes the run a COMMIT run."""
    _run(None, "--commit")
    assert _only_run().mode == CommandRun.MODE.COMMIT


def test_a_command_without_commit_is_read_only() -> None:
    """A command with no --commit flag cannot write, so it is READ_ONLY."""
    call_command(_ReadOnly(), stdout=StringIO())
    assert _only_run().mode == CommandRun.MODE.READ_ONLY


def test_a_dry_run_that_rolls_back_keeps_its_row() -> None:
    """The row is written outside the command's own transaction."""

    def roll_back(cmd: _Probe, options: dict[str, Any]) -> None:
        with transaction.atomic():
            LocationFactory.create()
            transaction.set_rollback(True)

    _run(roll_back)
    assert not Location.objects.exists()
    assert _only_run().status == CommandRun.STATUS.SUCCEEDED


def test_counts_are_recorded() -> None:
    """``tally`` accumulates named counts onto the row."""

    def count(cmd: _Probe, options: dict[str, Any]) -> None:
        cmd.tally("relinked")
        cmd.tally("relinked", 2)
        cmd.tally("skipped", 0)

    command = _run(count)
    assert _only_run().counts == {"relinked": 3, "skipped": 0}
    assert command.counts == {"relinked": 3, "skipped": 0}


def test_a_failed_tally_makes_a_successful_run_partial() -> None:
    """Some records failing is not a clean success."""
    _run(lambda cmd, options: cmd.tally("failed"))
    run = _only_run()
    assert run.status == CommandRun.STATUS.PARTIAL
    assert run.exit_code == 0


def test_output_is_kept() -> None:
    """The row keeps what the command printed."""
    _run()
    assert "probe ran" in _only_run().output_tail


# -- failures keep their exit code --------------------------------------------


def test_a_command_error_is_recorded_and_re_raised() -> None:
    """The row says FAILED with the error; the caller still sees it."""

    def fail(cmd: _Probe, options: dict[str, Any]) -> None:
        raise CommandError("nope", returncode=3)

    with pytest.raises(CommandError, match="nope"):
        _run(fail)
    run = _only_run()
    assert run.status == CommandRun.STATUS.FAILED
    assert run.exit_code == 3
    assert "nope" in run.error


def test_a_command_error_after_some_failures_is_partial() -> None:
    """relink's shape: it counts failures, finishes the rest, then raises."""

    def fail_some(cmd: _Probe, options: dict[str, Any]) -> None:
        cmd.tally("relinked", 5)
        cmd.tally("failed")
        raise CommandError("1 failed")

    with pytest.raises(CommandError):
        _run(fail_some)
    run = _only_run()
    assert run.status == CommandRun.STATUS.PARTIAL
    assert run.exit_code == 1
    assert run.counts == {"relinked": 5, "failed": 1}


def test_an_unexpected_exception_is_failed() -> None:
    """Any other exception is FAILED with exit code 1."""

    def crash(cmd: _Probe, options: dict[str, Any]) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _run(crash)
    run = _only_run()
    assert run.status == CommandRun.STATUS.FAILED
    assert run.exit_code == 1
    assert "ValueError: boom" in run.error


def test_a_keyboard_interrupt_is_interrupted() -> None:
    """Ctrl-C is INTERRUPTED, and still interrupts."""

    def interrupt(cmd: _Probe, options: dict[str, Any]) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(interrupt)
    run = _only_run()
    assert run.status == CommandRun.STATUS.INTERRUPTED
    assert run.exit_code == 130


@pytest.mark.parametrize(
    ("code", "status", "exit_code"),
    [
        (0, CommandRun.STATUS.SUCCEEDED, 0),
        (None, CommandRun.STATUS.SUCCEEDED, 0),
        (4, CommandRun.STATUS.FAILED, 4),
        ("bad", CommandRun.STATUS.FAILED, 1),
    ],
)
def test_a_system_exit_records_its_code(code: Any, status: str, exit_code: int) -> None:
    """``sys.exit`` inside a command keeps its code."""

    def leave(cmd: _Probe, options: dict[str, Any]) -> None:
        raise SystemExit(code)

    with pytest.raises(SystemExit):
        _run(leave)
    run = _only_run()
    assert run.status == status
    assert run.exit_code == exit_code


# -- the register never breaks a command -------------------------------------


def test_a_row_that_cannot_be_opened_does_not_stop_the_command() -> None:
    """The command runs unrecorded when the row cannot be written."""
    with mock.patch.object(CommandRun.objects, "create", side_effect=RuntimeError):
        command = _run(lambda cmd, options: cmd.tally("done"))
    assert command.counts == {"done": 1}
    assert command.current_run_id is None
    assert not CommandRun.objects.exists()


def test_a_row_that_cannot_be_closed_does_not_change_the_outcome() -> None:
    """A failed close is logged; the command's own result stands."""
    with mock.patch("django.db.models.query.QuerySet.update", side_effect=RuntimeError):
        _run()
    assert _only_run().status == CommandRun.STATUS.RUNNING


def test_a_command_can_opt_out() -> None:
    """``record_runs = False`` writes no row."""
    call_command(_Unrecorded(), stdout=StringIO())
    assert not CommandRun.objects.exists()


# -- who, where, which build -------------------------------------------------


@override_settings(RELEASE_VERSION="abc123", APP_RELEASE="45")
def test_the_build_is_stamped() -> None:
    """The row carries the git SHA and the release number."""
    _run()
    run = _only_run()
    assert run.git_sha == "abc123"
    assert run.release == "45"
    assert run.host
    assert run.environment == "development"


def test_the_operator_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SNOWDESK_OPERATOR names the person a shared OS user cannot."""
    monkeypatch.setenv("SNOWDESK_OPERATOR", "hugo")
    _run()
    assert _only_run().operator == "hugo"


def test_the_operator_falls_back_to_the_os_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the variable the OS user is recorded, or nothing if unknown."""
    monkeypatch.delenv("SNOWDESK_OPERATOR", raising=False)
    with mock.patch("apps.core.command_register.getpass.getuser", side_effect=OSError):
        _run()
    assert _only_run().operator == ""


def test_render_service_and_instance_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render's own variables say which service and box ran it."""
    monkeypatch.setenv("RENDER_SERVICE_NAME", "snowdesk-website")
    monkeypatch.setenv("RENDER_INSTANCE_ID", "srv-1-abc")
    _run()
    run = _only_run()
    assert (run.service, run.instance) == ("snowdesk-website", "srv-1-abc")


def test_call_command_is_recorded_as_call_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with no command line and no named caller is CALL_COMMAND."""
    monkeypatch.delenv("SNOWDESK_INVOKED_VIA", raising=False)
    _run()
    run = _only_run()
    assert run.invoked_via == CommandRun.INVOKED_VIA.CALL_COMMAND
    assert run.argv == ""


def test_a_shell_run_keeps_its_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manage.py run is SHELL, with the command line as typed."""
    monkeypatch.delenv("SNOWDESK_INVOKED_VIA", raising=False)
    command = _Probe()
    with mock.patch("sys.stdout", new_callable=StringIO):
        command.run_from_argv(["manage.py", "_probe", "--commit"])
    run = _only_run()
    assert run.invoked_via == CommandRun.INVOKED_VIA.SHELL
    assert run.argv == "test_command_register --commit"
    assert run.mode == CommandRun.MODE.COMMIT


@pytest.mark.parametrize("value", ["SCHEDULER", "scheduler", " Scheduler "])
def test_a_named_caller_wins(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """SNOWDESK_INVOKED_VIA names the scheduler or the build, in any case."""
    monkeypatch.setenv("SNOWDESK_INVOKED_VIA", value)
    _run()
    assert _only_run().invoked_via == CommandRun.INVOKED_VIA.SCHEDULER


def test_an_unknown_caller_name_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value outside the choices falls back to working it out."""
    monkeypatch.setenv("SNOWDESK_INVOKED_VIA", "cron")
    _run()
    assert _only_run().invoked_via == CommandRun.INVOKED_VIA.CALL_COMMAND


def test_a_nested_run_records_its_parent() -> None:
    """A command started by another command points at the run that started it."""

    def start_child(cmd: _Probe, options: dict[str, Any]) -> None:
        call_command(_ReadOnly(), stdout=StringIO())

    _run(start_child)
    parent = CommandRun.objects.get(mode=CommandRun.MODE.DRY_RUN)
    child = CommandRun.objects.get(mode=CommandRun.MODE.READ_ONLY)
    assert child.parent == parent
    assert child.invoked_via == CommandRun.INVOKED_VIA.CALL_COMMAND


def test_a_real_command_records_its_name_and_app() -> None:
    """The name and app label come from the command's module."""
    call_command("prune_command_runs", stdout=StringIO())
    run = CommandRun.objects.get(command="prune_command_runs")
    assert run.app_label == "core"


# -- arguments ----------------------------------------------------------------


def test_arguments_drop_plumbing_and_redact_secrets() -> None:
    """The row keeps what the command was asked to do, never a credential."""
    _run(None, "--commit", api_key="sk-live-123")
    arguments = _only_run().arguments
    assert arguments["commit"] is True
    assert arguments["api_key"] == REDACTED
    assert "stdout" not in arguments
    assert "skip_checks" not in arguments


def test_recorded_arguments_keep_empty_secrets_and_positionals() -> None:
    """An unset secret is not worth hiding; positional arguments are kept."""
    recorded = recorded_arguments(("a", 1), {"token": "", "when": object()})
    assert recorded["token"] == ""
    assert recorded["args"] == ["a", 1]
    assert isinstance(recorded["when"], str)


# -- the output tail ------------------------------------------------------------


def test_the_tail_keeps_the_end_without_colour_codes() -> None:
    """Only the last ``limit`` characters survive, ANSI codes stripped."""
    tail = _Tail(10)
    tail.add("\x1b[32m" + "a" * 20 + "\x1b[0m")
    tail.add("0123456789")
    assert tail.text() == "0123456789"


def test_a_long_run_keeps_only_the_tail() -> None:
    """A noisy run's row holds at most OUTPUT_TAIL_CHARS of output."""

    def noisy(cmd: _Probe, options: dict[str, Any]) -> None:
        for i in range(3000):
            cmd.stdout.write(f"line {i:05d} " + "x" * 10)
        cmd.stdout.write("the end")

    _run(noisy)
    tail = _only_run().output_tail
    assert len(tail) <= OUTPUT_TAIL_CHARS
    assert tail.rstrip().endswith("the end")
    assert "line 00000" not in tail


# -- the model ----------------------------------------------------------------


def test_to_string_names_the_command_mode_and_status() -> None:
    """``str()`` reads as the command, its mode and its outcome."""
    _run()
    text = str(_only_run())
    assert text.startswith("test_command_register [DRY_RUN] SUCCEEDED")


def test_a_running_row_has_no_duration() -> None:
    """An open run has no duration yet."""
    run = CommandRunFactory.create(status=CommandRun.STATUS.RUNNING, finished_at=None)
    assert run.duration is None
    assert "RUNNING" in run.to_string()


def test_the_queryset_filters() -> None:
    """The named filters select by command, mode and state."""
    committed = CommandRunFactory.create(mode=CommandRun.MODE.COMMIT)
    dry = CommandRunFactory.create(mode=CommandRun.MODE.DRY_RUN, command="other")
    running = CommandRunFactory.create(
        status=CommandRun.STATUS.RUNNING, finished_at=None
    )
    assert set(CommandRun.objects.committed()) == {committed, running}
    assert list(CommandRun.objects.dry_runs()) == [dry]
    assert list(CommandRun.objects.for_command("other")) == [dry]
    assert list(CommandRun.objects.running()) == [running]


def test_a_command_line_secret_is_redacted() -> None:
    """A credential typed on the command line never reaches the row."""
    assert redacted_argv(["--api-key", "sk-1", "--commit", "--token=abc", "x"]) == [
        "--api-key",
        REDACTED,
        "--commit",
        f"--token={REDACTED}",
        "x",
    ]
