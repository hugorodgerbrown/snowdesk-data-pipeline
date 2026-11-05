"""
tests/public/management/commands/test_check_ingest.py — check_ingest (SNOW-1423).

The command exits cleanly when no feed is red, raises naming each red feed
otherwise, never alerts on an off-season provider, and registers its run.
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import CommandRun

_TARGET = "apps.public.management.commands.check_ingest.build_report"


def _report(*rows: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal report holding ``rows`` in one group."""
    return {
        "groups": [{"name": "Bulletins", "feeds": list(rows)}],
        "summary": "All feeds green.",
    }


def _row(label: str, status: str, reason: str | None = None) -> dict[str, Any]:
    """Return one feed row with the fields the command reads."""
    return {"label": label, "status": status, "reason": reason}


@pytest.mark.django_db
class TestCheckIngest:
    """check_ingest's exit and its register row."""

    def test_green_exits_cleanly(self) -> None:
        """No red feed: a summary line, no error, feeds_red 0."""
        out = StringIO()
        report = _report(_row("SLF", "ok"), _row("ALBINA", "off", "off-season"))
        with mock.patch(_TARGET, return_value=report):
            call_command("check_ingest", stdout=out)
        assert "All feeds green." in out.getvalue()
        run = CommandRun.objects.for_command("check_ingest").get()
        assert run.status == CommandRun.STATUS.SUCCEEDED
        assert run.mode == CommandRun.MODE.READ_ONLY
        assert run.counts == {"feeds_red": 0}

    def test_red_raises_naming_each_feed(self) -> None:
        """Each red feed is named with its reason in the error."""
        report = _report(
            _row("SLF", "red", "last issue 2 d ago"),
            _row("Backfill", "red", "last run 3 h ago"),
            _row("ALBINA", "warn", "1 failed run(s)"),
        )
        with (
            mock.patch(_TARGET, return_value=report),
            pytest.raises(CommandError) as raised,
        ):
            call_command("check_ingest", stdout=StringIO())
        message = str(raised.value)
        assert "2 red feed(s)" in message
        assert "SLF (last issue 2 d ago)" in message
        assert "Backfill (last run 3 h ago)" in message
        assert "ALBINA" not in message
        run = CommandRun.objects.for_command("check_ingest").get()
        assert run.status == CommandRun.STATUS.FAILED
        assert run.counts == {"feeds_red": 2}

    def test_window_is_passed_through(self) -> None:
        """--window reaches the report."""
        with mock.patch(_TARGET, return_value=_report()) as build:
            call_command("check_ingest", "--window", "1h", stdout=StringIO())
        assert build.call_args.args == ("1h",)

    def test_verbose_lists_every_feed(self) -> None:
        """At verbosity 2 each feed's state is printed."""
        out = StringIO()
        with mock.patch(_TARGET, return_value=_report(_row("SLF", "ok"))):
            call_command("check_ingest", verbosity=2, stdout=out)
        assert "SLF: ok" in out.getvalue()

    def test_an_empty_platform_is_red(self) -> None:
        """Unpatched: with no runs at all, the hourly jobs are red."""
        with pytest.raises(CommandError, match="Estate mirror \\(never run\\)"):
            call_command("check_ingest", stdout=StringIO())
