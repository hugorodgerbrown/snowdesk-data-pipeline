"""
apps/core/command_register.py — The management command run register (SNOW-1189).

Backfills, relinks and reloads are run by hand against each environment
after a deploy, and until now nothing recorded that they had been: whether
``relink_bulletin_regions --commit`` had run on production, against which
build, and what it changed was a question for someone's memory or for
scrolling a Render shell. ``RegisteredCommand`` turns every run into a
``CommandRun`` row, so the question becomes a query.

Every project command inherits ``RegisteredCommand`` instead of
``BaseCommand`` (``tests/core/test_command_register_guard.py`` fails one
that does not). The class wraps ``execute()``, which both ``manage.py`` and
``call_command`` go through, so a command needs no other change to be
recorded. To record WHAT it changed, a command calls ``self.tally(name)``
as it goes; the counts land in the row's ``counts``. A ``failed`` tally
above zero makes the run ``PARTIAL`` rather than ``SUCCEEDED``.

Four rules shape the wrapper:

1. **A dry run is recorded.** It opens and closes its row outside the
   command's own transactions, so a command that rolls back without
   ``--commit`` does not take its row with it. (django-command-log, the
   package this replaces as a candidate, writes its row inside the atomic
   block and loses every dry run.) A run called inside an OUTER
   transaction does share that transaction's fate.
2. **The exit code is untouched.** Whatever ``handle()`` raises is recorded
   and re-raised, so cron and CI still see a non-zero exit (CLAUDE.md
   management-command rule 4).
3. **The register never breaks a command.** A failure to write a row — no
   database, a broken transaction — is logged and the command carries on
   unrecorded.
4. **The build is stamped, not guessed.** ``settings.RELEASE_VERSION``
   (Render's ``RENDER_GIT_COMMIT``) and ``settings.APP_RELEASE`` (the
   ``VERSION`` file) are both known when the process starts.

Who started a run comes from two environment variables: ``SNOWDESK_OPERATOR``
names the person (a Render shell runs every operator as one OS user, which
is the fallback), and ``SNOWDESK_INVOKED_VIA`` names the caller when it is
not a plain shell — ``run_scheduler`` sets ``SCHEDULER`` for its process,
``bin/build.sh`` sets ``BUILD``.
"""

from __future__ import annotations

import contextvars
import getpass
import json
import logging
import os
import re
import socket
import traceback
from typing import Any, ClassVar, TextIO, cast

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import CommandRun

logger = logging.getLogger(__name__)

# The most of a run's stdout and stderr kept on its row: enough for the
# last few hundred countdown lines of ``iterate_rows`` and the summary,
# without storing megabytes for a long backfill.
OUTPUT_TAIL_CHARS = 16 * 1024

# Django's own plumbing options. They say how the command was wired up,
# not what it was asked to do, so they are left out of ``arguments``.
_PLUMBING_OPTIONS = frozenset(
    {
        "force_color",
        "no_color",
        "pythonpath",
        "settings",
        "skip_checks",
        "stderr",
        "stdout",
        "traceback",
    }
)

# An option whose name looks like it carries a credential is stored as
# this marker rather than its value.
_SECRET_NAME = re.compile(r"key|token|secret|password", re.IGNORECASE)
REDACTED = "[redacted]"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

# The run currently executing in this context, so a command started from
# another command's ``call_command`` records which run started it.
_CURRENT_RUN: contextvars.ContextVar[CommandRun | None] = contextvars.ContextVar(
    "current_command_run", default=None
)


class _Tail:
    """Keep the last ``limit`` characters written to it."""

    def __init__(self, limit: int) -> None:
        """Start an empty tail holding at most ``limit`` characters."""
        self.limit = limit
        self._chunks: list[str] = []
        self._size = 0

    def add(self, text: str) -> None:
        """Append ``text``, dropping whole chunks from the front past the limit."""
        self._chunks.append(text)
        self._size += len(text)
        while self._size - len(self._chunks[0]) >= self.limit:
            self._size -= len(self._chunks.pop(0))

    def text(self) -> str:
        """Return the kept text, cut to the limit and free of colour codes."""
        return _ANSI_ESCAPE.sub("", "".join(self._chunks))[-self.limit :]


class _Tee:
    """A stream that writes through to ``target`` and copies into a ``_Tail``."""

    def __init__(self, target: TextIO, tail: _Tail) -> None:
        """Wrap ``target``, copying everything written into ``tail``."""
        self._target = target
        self._tail = tail

    def write(self, text: str) -> int:
        """Write ``text`` to the target and keep a copy."""
        self._tail.add(text)
        return self._target.write(text)

    def flush(self) -> None:
        """Flush the target."""
        self._target.flush()

    def isatty(self) -> bool:
        """Answer for the target, so Django's colour decision is unchanged."""
        isatty = getattr(self._target, "isatty", None)
        return bool(isatty and isatty())

    def __getattr__(self, name: str) -> Any:
        """Delegate anything else to the target stream."""
        return getattr(self._target, name)


def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
    """Return ``value`` as something a JSONField can store."""
    safe: dict[str, Any] = json.loads(json.dumps(value, default=str))
    return safe


def recorded_arguments(
    args: tuple[Any, ...], options: dict[str, Any]
) -> dict[str, Any]:
    """Return the arguments worth recording, with secrets redacted.

    Args:
        args: The positional arguments the command was given.
        options: The parsed options.

    Returns:
        The options minus Django's plumbing, plus ``args`` when there were
        any, JSON-safe.

    """
    recorded: dict[str, Any] = {}
    for name, value in sorted(options.items()):
        if name in _PLUMBING_OPTIONS:
            continue
        if _SECRET_NAME.search(name) and value not in (None, "", False):
            recorded[name] = REDACTED
        else:
            recorded[name] = value
    if args:
        recorded["args"] = list(args)
    return _json_safe(recorded)


def redacted_argv(argv: list[str]) -> list[str]:
    """Return ``argv`` with the value of any secret-looking flag redacted.

    Covers both ``--api-key=VALUE`` and ``--api-key VALUE``.

    Args:
        argv: The command-line arguments after the command's name.

    Returns:
        A copy with each secret value replaced by ``REDACTED``.

    """
    redacted: list[str] = []
    hide_next = False
    for token in argv:
        if hide_next:
            redacted.append(REDACTED)
            hide_next = False
            continue
        flag, sep, _value = token.partition("=")
        is_secret_flag = flag.startswith("-") and bool(_SECRET_NAME.search(flag))
        if is_secret_flag and sep:
            redacted.append(f"{flag}={REDACTED}")
        else:
            redacted.append(token)
            hide_next = is_secret_flag
    return redacted


def _operator() -> str:
    """Return who is running the command: SNOWDESK_OPERATOR, else the OS user."""
    named = os.environ.get("SNOWDESK_OPERATOR", "").strip()
    if named:
        return named
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — getuser raises OSError, KeyError or ImportError by platform; an unknown user is not worth failing a command for
        return ""


def _environment() -> str:
    """Return the settings overlay's name, e.g. ``production``."""
    module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    return module.rsplit(".", 1)[-1]


class RegisteredCommand(BaseCommand):
    """A management command whose every run is recorded as a ``CommandRun``.

    Subclass it exactly as ``BaseCommand``. Call ``self.tally(name)`` for
    each record a run changes (or, in a dry run, would change). Set
    ``record_runs = False``, with a comment saying why, only for a command
    that never finishes (``run_scheduler``) or must not touch the database.
    """

    record_runs: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise the command with an empty tally."""
        super().__init__(*args, **kwargs)
        self._counts: dict[str, int] = {}
        self._argv: list[str] | None = None
        self._run: CommandRun | None = None

    def tally(self, name: str, n: int = 1) -> None:
        """Add ``n`` to the run's count called ``name``.

        Args:
            name: What was counted, e.g. ``relinked``. A count called
                ``failed`` above zero marks the run ``PARTIAL``.
            n: How many to add.

        """
        self._counts[name] = self._counts.get(name, 0) + n

    @property
    def current_run_id(self) -> int | None:
        """Return the primary key of this run's row, or None if unrecorded."""
        return self._run.pk if self._run is not None else None

    @property
    def counts(self) -> dict[str, int]:
        """Return a copy of the run's counts so far."""
        return dict(self._counts)

    def run_from_argv(self, argv: list[str]) -> None:
        """Keep the command line so the row can show it as typed."""
        # argv is [prog, subcommand, *arguments]; the name is added back on write.
        self._argv = list(argv[2:])
        super().run_from_argv(argv)

    def execute(self, *args: Any, **options: Any) -> Any:
        """Run the command inside an open ``CommandRun`` row.

        Args:
            *args: Positional arguments, passed through.
            **options: Parsed options, passed through.

        Returns:
            Whatever ``BaseCommand.execute`` returns.

        Raises:
            BaseException: Anything the command raises, re-raised after the
                row records it.

        """
        if not self.record_runs:
            return super().execute(*args, **options)

        self._counts = {}
        tail = _Tail(OUTPUT_TAIL_CHARS)
        # ``OutputWrapper._out`` is the stream BaseCommand was built with;
        # django-stubs types it loosely because the wrapper proxies getattr.
        stdout = cast(TextIO, options.get("stdout") or self.stdout._out)
        stderr = cast(TextIO, options.get("stderr") or self.stderr._out)
        options["stdout"] = _Tee(stdout, tail)
        options["stderr"] = _Tee(stderr, tail)

        run = self._open_run(args, options)
        self._run = run
        token = _CURRENT_RUN.set(run)
        try:
            output = super().execute(*args, **options)
        except BaseException as exc:
            status, exit_code = self._classify(exc)
            self._close_run(
                run,
                status=status,
                exit_code=exit_code,
                tail=tail,
                error="".join(traceback.format_exception(exc))[-OUTPUT_TAIL_CHARS:],
            )
            raise
        finally:
            _CURRENT_RUN.reset(token)
        status = (
            CommandRun.STATUS.PARTIAL
            if self._counts.get("failed", 0) > 0
            else CommandRun.STATUS.SUCCEEDED
        )
        self._close_run(run, status=status, exit_code=0, tail=tail, error="")
        return output

    # -- the row ------------------------------------------------------------

    @property
    def command_name(self) -> str:
        """Return the command's name: the last part of its module path."""
        return self.__module__.rsplit(".", 1)[-1]

    @property
    def app_label(self) -> str:
        """Return the label of the app the command belongs to."""
        config = apps.get_containing_app_config(self.__module__)
        return config.label if config else ""

    def _mode(self, options: dict[str, Any]) -> str:
        """Return COMMIT or DRY_RUN for a ``--commit`` command, else READ_ONLY."""
        if "commit" not in options:
            return CommandRun.MODE.READ_ONLY
        return CommandRun.MODE.COMMIT if options["commit"] else CommandRun.MODE.DRY_RUN

    def _invoked_via(self, parent: CommandRun | None) -> str:
        """Return what started the run."""
        named = os.environ.get("SNOWDESK_INVOKED_VIA", "").strip().upper()
        if named in CommandRun.INVOKED_VIA.values:
            return named
        if parent is None and self._argv is not None:
            return CommandRun.INVOKED_VIA.SHELL
        return CommandRun.INVOKED_VIA.CALL_COMMAND

    def _open_run(
        self, args: tuple[Any, ...], options: dict[str, Any]
    ) -> CommandRun | None:
        """Create the ``RUNNING`` row, or return None when it cannot be written."""
        parent = _CURRENT_RUN.get()
        try:
            return CommandRun.objects.create(
                command=self.command_name,
                app_label=self.app_label,
                arguments=recorded_arguments(args, options),
                argv=(
                    " ".join([self.command_name, *redacted_argv(self._argv)])
                    if self._argv is not None
                    else ""
                ),
                mode=self._mode(options),
                invoked_via=self._invoked_via(parent),
                operator=_operator(),
                host=socket.gethostname(),
                service=os.environ.get("RENDER_SERVICE_NAME", ""),
                instance=os.environ.get("RENDER_INSTANCE_ID", ""),
                environment=_environment(),
                git_sha=str(settings.RELEASE_VERSION),
                release=str(settings.APP_RELEASE),
                parent=parent,
            )
        except Exception:  # noqa: BLE001 — rule 3: the register must never stop a command running
            logger.warning(
                "command register: could not open a run for %s",
                self.command_name,
                exc_info=True,
            )
            return None

    def _classify(self, exc: BaseException) -> tuple[str, int]:
        """Return the status and exit code an exception gives a run."""
        if isinstance(exc, KeyboardInterrupt):
            return CommandRun.STATUS.INTERRUPTED, 130
        if isinstance(exc, SystemExit):
            code = (
                exc.code
                if isinstance(exc.code, int)
                else (0 if exc.code is None else 1)
            )
            status = (
                CommandRun.STATUS.SUCCEEDED if code == 0 else CommandRun.STATUS.FAILED
            )
            return status, code
        exit_code = exc.returncode if isinstance(exc, CommandError) else 1
        # A command that counted some failures and then raised (as
        # relink_bulletin_regions does) did the rest of its work.
        if isinstance(exc, CommandError) and self._counts.get("failed", 0) > 0:
            return CommandRun.STATUS.PARTIAL, exit_code
        return CommandRun.STATUS.FAILED, exit_code

    def _close_run(
        self,
        run: CommandRun | None,
        *,
        status: str,
        exit_code: int,
        tail: _Tail,
        error: str,
    ) -> None:
        """Write the run's outcome onto its row."""
        if run is None:
            return
        now = timezone.now()
        try:
            CommandRun.objects.filter(pk=run.pk).update(
                status=status,
                exit_code=exit_code,
                finished_at=now,
                counts=dict(self._counts),
                output_tail=tail.text(),
                error=error,
                updated_at=now,
            )
        except Exception:  # noqa: BLE001 — rule 3: the register must never change a command's outcome
            logger.warning(
                "command register: could not close run %s of %s",
                run.pk,
                self.command_name,
                exc_info=True,
            )
