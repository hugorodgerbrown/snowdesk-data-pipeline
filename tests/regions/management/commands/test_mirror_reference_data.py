"""
tests/regions/management/commands/test_mirror_reference_data.py — The mirror_reference_data command (SNOW-1271).

Read-only by default: the mirror runs and is rolled back, so the preview
reports what a commit would change and writes nothing. ``--commit`` keeps
it. Every outcome is tallied, zeros included.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.core.models import CommandRun
from apps.ingest.public import SourceLocation, SourcePass, SourceResort
from apps.regions.models import Resort
from tests.factories import PassFactory, ResortFactory

pytestmark = pytest.mark.django_db


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("mirror_reference_data", *args, stdout=out)
    return out.getvalue()


def test_a_preview_reports_and_writes_nothing() -> None:
    """The counts are a commit's; every table is left as it was."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)
    resort.passes.add(PassFactory.create())
    out = _run()
    assert "[READ-ONLY]" in out
    assert "resorts: 1 created" in out
    assert "passes: 1 created" in out
    assert SourceResort.objects.count() == 0
    assert SourcePass.objects.count() == 0
    assert SourceLocation.objects.count() == 0
    assert Resort.objects.reference_data().count() == 0


def test_commit_writes_and_tallies() -> None:
    """With --commit the rows stay, and the register has every count."""
    ResortFactory.create(latitude=46.1, longitude=7.4)
    _run("--commit")
    assert SourceResort.objects.count() == 1
    assert SourceLocation.objects.count() == 1
    run = CommandRun.objects.filter(command="mirror_reference_data").get()
    assert run.counts["resorts_created"] == 1
    assert run.counts["locations_created"] == 1
    assert run.counts["passes_created"] == 0
