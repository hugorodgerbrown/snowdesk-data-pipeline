"""
tests/seeding.py — Shared helper to build the navigable test dataset.

Replaces the old ``loaddata test_data`` fixture path: loads the CH region/resort
reference data, then runs ``seed_test_data --all --commit`` to build the full
bulletin/weather/point/favourite dataset from the factories. Used by the smoke,
home-page, and e2e tests that need a navigable database rather than a handful of
hand-built factory rows.
"""

from __future__ import annotations

from django.core.management import call_command


def seed_test_dataset() -> None:
    """Load region reference data and seed the full factory dataset.

    Mirrors what a fresh dev/CI database gets: ``loaddata eaws_CH resorts``
    followed by ``seed_test_data --all --commit``. Requires ``DEBUG=True`` (the
    default in the test settings), which ``seed_test_data`` enforces.
    """
    call_command("loaddata", "eaws_CH", "resorts", verbosity=0)
    call_command("seed_test_data", "--all", commit=True, verbosity=0)
