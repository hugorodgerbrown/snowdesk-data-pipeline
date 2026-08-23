# conftest.py — shared fixtures for the regions test package.
#
# Provides ``load_eaws_fixture_once``, the factory behind the per-country
# fixture-smoke modules (test_austria_fixture.py and its siblings).
"""Shared fixtures for the regions tests.

The per-country fixture-smoke modules each assert a different property of
the same ``loaddata`` result — row counts, FK navigability, canonical
names. Running ``loaddata`` inside every one of them meant loading the
same EAWS JSON six or seven times per module: around 0.6s a load, and
four countries' worth of it. ``load_eaws_fixture_once`` loads it once for
the module instead, committing outside any test transaction so each
test's own writes still roll back around it, and flushing on teardown so
the worker database is empty for whatever runs next.

The load itself stays covered: it is still a real ``loaddata`` of the
committed fixture, and the ``*_fixture_loads`` test in each module still
asserts the row counts it produces. What is gone is the repetition, not
the assertion.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from django.core.management import call_command
from pytest_django import DjangoDbBlocker


@pytest.fixture(scope="module")
def load_eaws_fixture_once(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[Callable[[str], None]]:
    """Return a loader that runs ``loaddata`` once per module, then cleans up.

    Args:
        django_db_setup: pytest-django's database-creation fixture, depended
            on so the database exists before the load runs.
        django_db_blocker: pytest-django's guard, unblocked so the load can
            commit outside any individual test's transaction.

    Yields:
        A callable taking a fixture path to load.

    """
    del django_db_setup

    def _load(fixture_path: str) -> None:
        """Load one fixture into the worker database.

        Args:
            fixture_path: Path passed straight through to ``loaddata``.

        """
        with django_db_blocker.unblock():
            call_command("loaddata", fixture_path, verbosity=0)

    yield _load

    with django_db_blocker.unblock():
        call_command("flush", interactive=False, verbosity=0)
