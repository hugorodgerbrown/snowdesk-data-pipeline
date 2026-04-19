"""
tests/pipeline/management/commands/test_monitor_query_counts.py — Tests
for the monitor_query_counts management command.

Covers:
  - ``--commit`` writes the baseline file with one line per URL.
  - Read-only run against a matching baseline exits cleanly.
  - Read-only run against a mismatching baseline raises CommandError.
  - Missing baseline raises CommandError unless ``--commit`` is passed.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from pipeline.management.commands import monitor_query_counts as cmd_module


@pytest.fixture
def baseline_path(tmp_path: Path) -> Iterator[Path]:
    """Redirect the command's baseline path to a tmp location per test."""
    fake = tmp_path / "query_counts.txt"
    with patch.object(cmd_module, "BASELINE_PATH", fake):
        yield fake


@pytest.fixture
def monitored_urls() -> Iterator[list[tuple[str, str]]]:
    """Restrict the monitored URL list to a minimal, always-available pair."""
    minimal = [("home", "/"), ("map", "/map/")]
    with patch.object(cmd_module, "MONITORED_URLS", minimal):
        yield minimal


@pytest.mark.django_db
def test_commit_writes_baseline(baseline_path: Path, monitored_urls) -> None:
    """--commit writes one 'name count' line per monitored URL."""
    assert not baseline_path.exists()
    out = StringIO()
    call_command("monitor_query_counts", "--commit", stdout=out)

    assert baseline_path.exists()
    lines = [
        line.split()
        for line in baseline_path.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    names = {line[0] for line in lines}
    assert names == {"home", "map"}
    for _, count_str in lines:
        assert int(count_str) >= 0


@pytest.mark.django_db
def test_read_only_passes_when_baseline_matches(
    baseline_path: Path, monitored_urls
) -> None:
    """A read-only run against a matching baseline exits cleanly."""
    # Seed the baseline by running with --commit first.
    call_command("monitor_query_counts", "--commit", stdout=StringIO())
    # Subsequent read-only run should succeed.
    out = StringIO()
    call_command("monitor_query_counts", stdout=out)
    assert "All counts match the baseline." in out.getvalue()


@pytest.mark.django_db
def test_read_only_fails_on_mismatch(baseline_path: Path, monitored_urls) -> None:
    """Baseline with deliberately wrong numbers triggers CommandError."""
    baseline_path.write_text("home 999\nmap 999\n")
    with pytest.raises(CommandError, match="differ from the baseline"):
        call_command("monitor_query_counts", stdout=StringIO())


@pytest.mark.django_db
def test_read_only_fails_without_baseline(baseline_path: Path, monitored_urls) -> None:
    """Missing baseline + no --commit is a hard failure."""
    assert not baseline_path.exists()
    with pytest.raises(CommandError, match="No baseline"):
        call_command("monitor_query_counts", stdout=StringIO())


@pytest.mark.django_db
def test_malformed_baseline_raises(baseline_path: Path, monitored_urls) -> None:
    """A non-comment line without a count raises a clear CommandError."""
    baseline_path.write_text("home\n")
    with pytest.raises(CommandError, match="Malformed"):
        call_command("monitor_query_counts", stdout=StringIO())
