"""
tests/config/test_release_settings.py — Tests for the release-time settings.

``APP_RELEASED_AT`` reads the ``BUILT_AT`` file ``bin/build.sh`` writes
(SNOW-1191) so the ops MCP's ``build_info`` and ``/api/version`` report
when the build went live rather than when the process last booted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import base


def test_built_at_is_read_from_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stamp the build wrote is returned, stripped."""
    (tmp_path / "BUILT_AT").write_text("2026-10-22T09:00:00+00:00\n")
    monkeypatch.setattr(base, "BASE_DIR", tmp_path)

    assert base._built_at_from_file() == "2026-10-22T09:00:00+00:00"


def test_no_built_at_file_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a build stamp the reader returns "", so boot time is used."""
    monkeypatch.setattr(base, "BASE_DIR", tmp_path)

    assert base._built_at_from_file() == ""
