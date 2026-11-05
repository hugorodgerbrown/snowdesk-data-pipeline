"""
tests/core/test_command_errors.py — Tests for apps.core.command_errors (SNOW-1425).

``ReportedCommandError`` must stay a ``CommandError`` in every respect a
caller relies on: ``except CommandError`` catches it, and it keeps the
non-zero ``returncode`` Django exits with.
"""

from __future__ import annotations

import pytest
from django.core.management.base import CommandError

from apps.core.command_errors import ReportedCommandError


class TestReportedCommandError:
    """``ReportedCommandError`` behaves as a ``CommandError``."""

    def test_is_caught_as_a_command_error(self) -> None:
        """A plain ``except CommandError`` still catches it."""
        with pytest.raises(CommandError, match="already reported"):
            raise ReportedCommandError("already reported")

    def test_keeps_the_default_returncode(self) -> None:
        """It exits 1 like any CommandError, and honours an explicit code."""
        assert ReportedCommandError("x").returncode == 1
        assert ReportedCommandError("x", returncode=3).returncode == 3
