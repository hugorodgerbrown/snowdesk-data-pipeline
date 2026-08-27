"""
tests/core/management/commands/test_sync_from_production.py — command tests.

Covers:
  - The safety gate: with no ``production`` alias configured the command
    refuses rather than doing nothing quietly.
  - Argument validation: an unknown ``--only`` label and a non-positive
    ``--since-days``.
  - The read-only default: no ``--commit`` writes no rows.
  - The window: ``--all`` clears the ``since`` filter, ``--since-days``
    sets one.
  - The failure contract: skipped rows exit non-zero, so the Render cron job
    shows red rather than silently under-copying.

The copy engine itself is tested in
``tests/core/services/test_production_sync.py``; here it is stubbed so the
tests are about the command's own behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections

from apps.core.services.production_sync import IdMap, TableResult

COMMAND = "sync_from_production"
MODULE = "apps.core.management.commands.sync_from_production"


@pytest.fixture
def safe_gate(db: None, monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:
    """Configure a distinct production alias so the safety gate passes.

    ``SITE_ENVIRONMENT`` defaults to ``"production"`` in ``base.py`` (real
    deploys set it explicitly), which the gate correctly refuses — so the
    label has to be set here as well as the alias. The ``db`` dependency is
    for the per-table ``transaction.atomic()`` the command opens, not for
    any row it writes — ``sync_table`` is stubbed out in these tests.
    """
    settings.SITE_ENVIRONMENT = "staging"
    monkeypatch.setitem(
        connections.databases,
        "production",
        {"HOST": "prod.example", "PORT": "5432", "NAME": "snowdesk"},
    )
    monkeypatch.setattr(
        f"{MODULE}.build_id_map",
        lambda spec: IdMap(spec.name, spec.model_label, spec.natural_key, {}),
    )


def _stub_sync(
    monkeypatch: pytest.MonkeyPatch,
    *,
    skipped: int = 0,
    calls: list | None = None,
) -> None:
    """Replace ``sync_table`` with a recorder returning fixed counts."""

    def fake(spec: Any, *, id_maps: Any, since: Any, commit: bool) -> TableResult:
        if calls is not None:
            calls.append((spec.model_label, since, commit))
        return TableResult(label=spec.model_label, read=2, written=2, skipped=skipped)

    monkeypatch.setattr(f"{MODULE}.sync_table", fake)


def test_refuses_without_a_production_alias() -> None:
    """No configured production database is an error, not a silent no-op."""
    with pytest.raises(CommandError, match="No 'production' database"):
        call_command(COMMAND)


def test_rejects_an_unknown_only_label(safe_gate: None) -> None:
    """A typo in --only names the known tables rather than copying nothing."""
    with pytest.raises(CommandError, match="Not in the sync plan"):
        call_command(COMMAND, "--only", "bulletins.Nonexistent")


def test_rejects_a_non_positive_window(safe_gate: None) -> None:
    """--since-days 0 would read nothing; it is rejected up front."""
    with pytest.raises(CommandError, match="positive number of days"):
        call_command(COMMAND, "--since-days", "0")


def test_dry_run_does_not_commit(
    safe_gate: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --commit, every table is visited with ``commit=False``."""
    calls: list = []
    _stub_sync(monkeypatch, calls=calls)

    call_command(COMMAND, verbosity=0)

    assert calls, "no table was visited"
    assert all(commit is False for _, _, commit in calls)


def test_all_clears_the_update_window(
    safe_gate: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--all passes ``since=None``, so the whole table is read."""
    calls: list = []
    _stub_sync(monkeypatch, calls=calls)

    call_command(COMMAND, "--all", "--commit", verbosity=0)

    assert all(since is None for _, since, _ in calls)


def test_since_days_sets_a_window(
    safe_gate: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --all, every table is read through the same window start."""
    calls: list = []
    _stub_sync(monkeypatch, calls=calls)

    call_command(COMMAND, "--since-days", "3", "--commit", verbosity=0)

    windows = {since for _, since, _ in calls}
    assert len(windows) == 1
    assert windows.pop() is not None


def test_only_limits_the_plan(safe_gate: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """--only runs exactly the named tables, matched case-insensitively."""
    calls: list = []
    _stub_sync(monkeypatch, calls=calls)

    call_command(COMMAND, "--only", "bulletins.bulletin", "--commit", verbosity=0)

    assert [label for label, _, _ in calls] == ["bulletins.Bulletin"]


def test_skipped_rows_exit_non_zero(
    safe_gate: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partially-copied run raises, so cron reports it as a failure.

    Skipped rows mean a parent is missing on this database — copying on
    regardless would leave staging quietly short of data with a green run
    behind it.
    """
    _stub_sync(monkeypatch, skipped=1)

    with pytest.raises(CommandError, match="skipped"):
        call_command(COMMAND, "--commit", verbosity=0)


def test_dry_run_does_not_raise_on_skipped_rows(
    safe_gate: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip counts are meaningless in a dry run, so they are not an error.

    Nothing was written, so no child could resolve a new parent; failing on
    that would make the read-only mode useless against an empty database.
    """
    _stub_sync(monkeypatch, skipped=99)

    call_command(COMMAND, verbosity=0)
